from collections import defaultdict
from contextlib import contextmanager
from functools import partial
from math import ceil
import numpy as np
from nutils import function as fn, matrix, _
from operator import itemgetter


class MetaMu(type):

    def __getitem__(cls, val):
        return mu(itemgetter(val))

class mu(metaclass=MetaMu):

    def _wrap(func):
        def ret(*args):
            args = [arg if isinstance(arg, mu) else mu(arg) for arg in args]
            return func(*args)
        return ret

    def __init__(self, *args):
        if len(args) == 1:
            self.func = args[0]
            return
        self.oper, self.op1, self.op2 = args

    def __call__(self, p):
        if hasattr(self, 'oper'):
            if self.oper == '+':
                return self.op1(p) + self.op2(p)
            if self.oper == '-':
                return self.op1(p) - self.op2(p)
            if self.oper == '*':
                return self.op1(p) * self.op2(p)
            if self.oper == '/':
                return self.op1(p) / self.op2(p)
            raise ValueError(self.oper)
        if callable(self.func):
            return self.func(p)
        return self.func

    @_wrap
    def __add__(self, other):
        return mu('+', self, other)

    @_wrap
    def __radd__(self, other):
        return mu('+', other, self)

    @_wrap
    def __sub__(self, other):
        return mu('-', self, other)

    @_wrap
    def __rsub__(self, other):
        return mu('-', other, self)

    @_wrap
    def __mul__(self, other):
        return mu('*', self, other)

    @_wrap
    def __rmul__(self, other):
        return mu('*', other, self)

    @_wrap
    def __truediv__(self, other):
        return mu('/', self, other)

    @_wrap
    def __rtruediv__(self, other):
        return mu('/', other, self)


def num_elems(length, meshwidth, prescribed=None):
    if prescribed is not None:
        return prescribed
    return int(ceil(length / meshwidth))


def defaultdict_list():
    return defaultdict(list)


class Case:

    def __init__(self, domain, geom, bases, basis_lengths=None):
        self.domain = domain
        self.geom = geom
        self._integrands = defaultdict(defaultdict_list)
        self._computed = defaultdict(defaultdict_list)

        for field, basis in zip(self.fields, bases):
            setattr(self, field + 'basis', basis)
        if basis_lengths is None:
            assert len(bases) == 1
            basis_lengths = [bases[0].shape[0]]
        self.basis_lengths = basis_lengths

    def get(self, *args):
        return [self.__dict__[arg] for arg in args]

    @contextmanager
    def add_integrands(self, name, rhs=False):
        yield partial(self.add_integrand, name, rhs)

    def add_integrand(self, name, rhs, integrand, scale=None, domain=None, symmetric=False):
        if scale is None:
            scale = mu(1.0)
        if symmetric:
            integrand = integrand + integrand.T
        self._integrands[name][domain].append((integrand, scale))
        if rhs:
            lift_integrand = -(integrand * self.lift[_, :]).sum(1)
            self._integrands['lift-' + name][domain].append((lift_integrand, scale))

    def integrate(self, name, mu):
        ret_matrix = 0
        for dom, contents in self._integrands[name].items():
            integrands, scales = zip(*contents)
            if self._computed[name][dom]:
                matrices = self._computed[name][dom]
            else:
                domain = self._domain(dom)
                matrices = domain.integrate(integrands, geometry=self.geom, ischeme='gauss9')
                self._computed[name][dom] = matrices
            ret_matrix += sum(mm * scl(mu) for mm, scl in zip(matrices, scales))
        return ret_matrix

    def integrand(self, name, mu):
        ret_integrand = 0
        for dom, contents in self._integrands[name].items():
            indicator = self._indicator(dom)
            ret_integrand += sum(scl(mu) * itg for itg, scl in contents) * indicator
        return ret_integrand

    def mass(self, field):
        integrand = fn.outer(self.basis(field))
        while len(integrand.shape) > 2:
            integrand = integrand.sum(-1)
        return self.domain.integrate(integrand, geometry=self.geom, ischeme='gauss9').core

    def basis(self, name):
        assert name in self.fields
        return getattr(self, name + 'basis')

    def basis_indices(self, name):
        start = 0
        for field, length in zip(self.fields, self.basis_lengths):
            if field != name:
                start += length
            else:
                break
        return np.arange(start, start + length, dtype=np.int)

    def solution(self, lhs, lift=True):
        if lift:
            lhs = lhs + self.lift
        return [self.basis(field).dot(lhs) for field in self.fields]

    def _domain(self, dom):
        if dom is None:
            return self.domain
        if isinstance(dom, int):
            dom = (dom,)
        dom_str = ','.join('patch' + str(d) for d in dom)
        return self.domain[dom_str]

    def _indicator(self, dom):
        if dom is None:
            return 1
        if isinstance(dom, int):
            dom = (dom,)
        patches = self.domain.basis_patch()
        return patches.dot([1 if i in dom else 0 for i in range(len(patches))])



def _project_tensor(tensor, projection):
    if isinstance(tensor, (matrix.ScipyMatrix, matrix.NumpyMatrix)):
        return projection.T.dot(tensor.core.dot(projection))
    else:
        for ax in range(tensor.ndim):
            tensor = np.tensordot(tensor, projection, (0, 0))
        return tensor

class ProjectedCase(Case):

    def __init__(self, case, projection, fields, lengths):
        self.case = case
        self.projection = projection
        self.fields = fields
        self.basis_lengths = lengths

        self._computed = {}
        for part, domains in case._computed.items():
            proj_contents = []
            for dom, matrices in domains.items():
                __, scales = zip(*case._integrands[part][dom])
                proj_matrices = [
                    _project_tensor(matrix, projection)
                    for matrix in matrices
                ]
                proj_contents.extend(zip(proj_matrices, scales))
            self._computed[part] = proj_contents

    def integrate(self, name, mu):
        return sum(mm * scl(mu) for mm, scl in self._computed[name])
