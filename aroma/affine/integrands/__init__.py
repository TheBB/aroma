# Copyright (C) 2014 SINTEF ICT,
# Applied Mathematics, Norway.
#
# Contact information:
# E-mail: eivind.fonn@sintef.no
# SINTEF Digital, Department of Applied Mathematics,
# P.O. Box 4760 Sluppen,
# 7045 Trondheim, Norway.
#
# This file is part of AROMA.
#
# AROMA is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# AROMA is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with AROMA. If not, see
# <http://www.gnu.org/licenses/>.
#
# In accordance with Section 7(b) of the GNU General Public License, a
# covered work must retain the producer line in every data file that
# is created or manipulated using AROMA.
#
# Other Usage
# You can be released from the requirements of the license by purchasing
# a commercial license. Buying such a license is mandatory as soon as you
# develop commercial activities involving the AROMA library without
# disclosing the source code of your own applications.
#
# This file may be used in accordance with the terms contained in a
# written agreement between you and SINTEF Digital.


import numpy as np
from nutils import log, _
import scipy.sparse as sp

from aroma import util


class MetaIntegrand(type):

    def __new__(cls, name, bases, attrs):
        subclass = type.__new__(cls, name, bases, attrs)
        if name != 'Integrand':
            Integrand.subclasses[name] = subclass
        return subclass


class Integrand(metaclass=MetaIntegrand):

    subclasses = {}

    @classmethod
    def accepts(cls, obj):
        return False

    @staticmethod
    def get_subclass(obj):
        for subclass in Integrand.subclasses.values():
            if subclass.accepts(obj):
                return subclass
        return None

    @staticmethod
    def acceptable(obj):
        return isinstance(obj, Integrand) or (Integrand.get_subclass(obj) is not None)

    @staticmethod
    def make(obj):
        if isinstance(obj, Integrand):
            return obj
        if not Integrand.acceptable(obj):
            raise NotImplementedError
        return Integrand.get_subclass(obj)(obj)

    def __init__(self):
        self._properties = {}

    @staticmethod
    def read(group):
        type_ = group.attrs['type']
        subclass = util.find_subclass(Integrand, type_)
        return subclass.read(group)

    def prop(self, *args, **kwargs):
        if args:
            assert all(isinstance(arg, str) for arg in args)
            values = [(self._properties[arg] if arg in self._properties else kwargs[arg]) for arg in args]
            if len(args) == 1:
                return values[0]
            return values
        for key, val in kwargs.items():
            if key not in self._properties:
                self._properties[key] = val
        return self

    def write_props(self, group):
        group = group.require_group('properties')
        for key, value in self._properties.items():
            util.to_dataset(value, group, key)

    def read_props(self, group):
        self._properties = {}
        for key, value in group['properties'].items():
            self._properties[key] = util.from_dataset(value)

    def ensure_shareable(self):
        pass


class ThinWrapperIntegrand(Integrand):

    def __init__(self, obj):
        super().__init__()
        self.obj = obj

    @property
    def ndim(self):
        return len(self.shape)

    @property
    def shape(self):
        return self.obj.shape

    def write(self, group, name):
        sub = group.require_group(name)
        util.to_dataset(self.obj, sub, 'data')
        sub.attrs['type'] = self.__class__.__name__
        self.write_props(sub)
        return sub

    @staticmethod
    def read(group):
        cls = util.find_subclass(ThinWrapperIntegrand, group.attrs['type'])
        retval = cls.__new__(cls)
        retval.obj = util.from_dataset(group['data'])
        retval.read_props(group)
        return retval


class NumpyArrayIntegrand(ThinWrapperIntegrand):

    optimized = True

    @classmethod
    def accepts(cls, obj):
        return isinstance(obj, (np.ndarray,) + util._SCALARS)

    def __init__(self, obj):
        if isinstance(obj, np.ndarray):
            super().__init__(obj)
        else:
            super().__init__(np.array(obj))

    def _contract(self, contraction):
        axes, obj = [], self.obj
        for i, cont in enumerate(contraction):
            if cont is None:
                continue
            assert cont.ndim == 1
            for __ in range(i):
                cont = cont[_,...]
            while cont.ndim < self.ndim:
                cont = cont[...,_]
            obj = obj * cont
            axes.append(i)
        return obj.sum(tuple(axes))

    def get(self, contraction):
        return self._contract(contraction)

    def cache(self, **kwargs):
        return self

    def contract(self, contraction):
        return NumpyArrayIntegrand(self._contract(contraction))

    def project(self, projection):
        obj = self.obj
        s = slice(None)
        for i, p in enumerate(projection):
            if p is None:
                continue
            obj = obj[(s,)*i + (_,s,Ellipsis)]
            obj = obj * p[(_,)*i + (s,s) + (_,) * (self.ndim - i - 1)]
            obj = obj.sum(i+1)
        return NumpyArrayIntegrand(obj)


class ScipyArrayIntegrand(ThinWrapperIntegrand):

    optimized = True

    @classmethod
    def accepts(cls, obj):
        return isinstance(obj, sp.spmatrix)

    def get(self, contraction):
        if all(c is None for c in contraction):
            return self.obj
        ca, cb = contraction
        if ca is None:
            return self.obj.dot(cb)
        elif cb is None:
            return self.obj.T.dot(ca)
        return ca.dot(self.obj.dot(cb.T))

    def cache(self, **kwargs):
        return self

    def contract(self, contraction):
        assert len(contraction) == 2

        ca, cb = contraction
        if ca is None and cb is None:
            return self
        assert ca is None or cb is None

        if ca is None:
            return NumpyArrayIntegrand(self.obj.dot(cb))
        return NumpyArrayIntegrand(self.obj.T.dot(ca))

    def project(self, projection):
        if all(p is None for p in projection):
            return self
        pa, pb = projection
        if pa is None:
            return NumpyArrayIntegrand(self.obj.dot(pb.T))
        elif pb is None:
            return NumpyArrayIntegrand(self.obj.T.dot(pa.T).T)
        return NumpyArrayIntegrand(pa.dot(self.obj.dot(pb.T)))


class COOTensorIntegrand(Integrand):

    optimized = True

    def __init__(self, shape, *args):
        super().__init__()
        assert len(shape) == 3
        assert len(shape) == len(args) - 1
        self.shape = shape
        self.ndim = len(shape)

        nz = np.nonzero(args[-1])
        *indices, self.data = [arg[nz] for arg in args]

        fits = all(np.max(i) <= np.iinfo(np.int32).max for i in indices)
        idx_dtype = np.int32 if fits else np.int64
        indices = tuple(i.astype(idx_dtype, copy=True) for i in indices)
        self.indices = indices

        # TODO: Figure out in advance which assemblers we will need
        self.assemblers = {
            (1,): util.CSRAssembler((shape[0], shape[2]), indices[0], indices[2]),
            (2,): util.CSRAssembler((shape[0], shape[1]), indices[0], indices[1]),
            (1,2): util.VectorAssembler((shape[0],), indices[0])
        }

    def write(self, group, name):
        sub = group.require_group(name)
        datagrp = sub.require_group('data')
        util.to_dataset(self.indices[0], datagrp, 'indices-i')
        util.to_dataset(self.indices[1], datagrp, 'indices-j')
        util.to_dataset(self.indices[2], datagrp, 'indices-k')
        util.to_dataset(self.data, datagrp, 'data')
        datagrp.attrs['shape'] = self.shape
        sub.attrs['type'] = 'COOTensorIntegrand'

        assemblers = datagrp.require_group('assemblers')
        for key, assembler in self.assemblers.items():
            name = ','.join(str(s) for s in key)
            ass_grp = assemblers.require_group(name)
            assembler.write(ass_grp)

        return sub

    @staticmethod
    def read(group):
        datagrp = group['data']
        retval = COOTensorIntegrand.__new__(COOTensorIntegrand)
        retval.indices = datagrp['indices-i'][:], datagrp['indices-j'][:], datagrp['indices-k'][:]
        retval.data = datagrp['data'][:]
        retval.shape = tuple(datagrp.attrs['shape'])

        retval.assemblers = {}
        for key, grp in datagrp['assemblers'].items():
            key = tuple(int(i) for i in key.split(','))
            retval.assemblers[key] = getattr(util, grp.attrs['type']).read(grp)

        return retval

    def ensure_shareable(self):
        self.indices = tuple(util.shared_array(i) for i in self.indices)
        self.data = util.shared_array(self.data)
        for ass in self.assemblers.values():
            ass.ensure_shareable()

    def get(self, contraction):
        retval = self._contract(contraction)
        if not isinstance(retval, COOTensorIntegrand):
            return retval
        return retval.toarray()

    def toarray(self):
        # TODO: This could be more efficient, but this function should never be
        # called in performance-critical code anyway
        # Ravel down to a matrix, convert to scipy, then to numpy, then unravel
        flat_index = np.ravel_multi_index(self.indices[1:], self.shape[1:])
        flat_shape = (self.shape[0], np.product(self.shape[1:]))
        matrix = sp.coo_matrix((self.data, (self.indices[0], flat_index)), shape=flat_shape)
        matrix = matrix.toarray()
        return np.reshape(matrix, self.shape)

    def cache(self, **kwargs):
        return self

    def contract(self, contraction):
        return Integrand.make(self._contract(contraction))

    def _contract(self, contraction):
        if all(c is None for c in contraction):
            return self
        contraction = [(i, c) for i, c in enumerate(contraction) if c is not None]
        axes = tuple(i for i, __ in contraction)
        data = np.copy(self.data)
        for i, c in contraction:
            data *= c[self.indices[i]]
        if axes == (0,1,2):
            return np.sum(data)
        return self.assemblers[axes](data)

    def project(self, projection):
        # TODO: Remove this condition
        assert all(p is not None for p in projection)
        pa, pb, pc = projection
        P, __ = pa.shape
        ass = util.CSRAssembler(self.shape[1:], self.indices[1], self.indices[2])
        ret = np.empty((P, pb.shape[0], pc.shape[0]), self.data.dtype)
        for i in log.iter('index', range(P), length=P):
            data = self.data * pa[i, self.indices[0]]
            mx = ass(data)
            ret[i] = pb.dot(mx.dot(pc.T))
        return NumpyArrayIntegrand(ret)


class LazyIntegral:
    pass


def integrate(*args):
    if all(not isinstance(arg, LazyIntegral) for arg in args):
        return args
    assert all(arg.__class__ == args[0].__class__ for arg in args[1:])
    return args[0].__class__.integrate(*args)
