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


from itertools import count
import numpy as np
from nutils import function as fn, log, matrix

from aroma.affine import integrate


__all__ = ['stokes', 'navierstokes']


class IterationCountError(Exception):
    pass


def _stokes_matrix(case, mu):
    matrix = case['divergence'](mu, sym=True) + case['laplacian'](mu)
    if 'stab-lhs' in case:
        matrix += case['stab-lhs'](mu, sym=True)
    return matrix


def _stokes_rhs(case, mu):
    rhs = - case['divergence'](mu, lift=0) - case['laplacian'](mu, lift=1)
    if 'forcing' in case:
        rhs += case['forcing'](mu)
    if 'stab-lhs' in case:
        rhs -= case['stab-lhs'](mu, lift=1)
    if 'stab-rhs' in case:
        rhs += case['stab-rhs'](mu)
    return rhs


def _stokes_assemble(case, mu):
    return _stokes_matrix(case, mu), _stokes_rhs(case, mu)


def stokes(case, mu):
    assert 'divergence' in case
    assert 'laplacian' in case

    matrix, rhs = _stokes_assemble(case, mu)
    lhs = matrix.solve(rhs, constrain=case.cons)

    return lhs


def navierstokes(case, mu, newton_tol=1e-10, maxit=10):
    assert 'divergence' in case
    assert 'laplacian' in case
    assert 'convection' in case

    stokes_mat, stokes_rhs = _stokes_assemble(case, mu)
    lhs = stokes_mat.solve(stokes_rhs, constrain=case.cons)

    stokes_mat += case['convection'](mu, lift=1) + case['convection'](mu, lift=2)
    stokes_rhs -= case['convection'](mu, lift=(1,2))

    vmass = case.norm('v', 'h1s', mu=mu)

    def conv(lhs):
        c = case['convection']
        rh = c(mu, cont=(None, lhs, lhs))
        lh = c(mu, cont=(None, lhs, None)) + c(mu, cont=(None, None, lhs))
        rh, lh = integrate(rh, lh)
        return rh, lh

    for it in count(1):
        rh, lh = conv(lhs)
        rhs = stokes_rhs - stokes_mat.matvec(lhs) - rh
        ns_mat = stokes_mat + lh

        update = ns_mat.solve(rhs, constrain=case.cons)
        lhs += update

        update_norm = np.sqrt(vmass.matvec(update).dot(update))
        log.info('update: {:.2e}'.format(update_norm))
        if update_norm < newton_tol:
            break

        if it > maxit:
            raise IterationCountError

    return lhs


def blocksolve(vv, sv, sp, rhs, V, S, P):
    lhs = np.zeros_like(rhs)
    lhs[V] = vv.solve(rhs[V])
    lhs[P] = sp.solve(rhs[S] - sv.matvec(lhs[V]))
    return lhs


def navierstokes_block(case, mu, newton_tol=1e-10, maxit=10):
    for itg in ['laplacian-vv', 'laplacian-sv', 'divergence-sp',
                'convection-vvv', 'convection-svv']:
        assert itg in case

    nn = case.size // 3
    V = np.arange(nn)
    S = np.arange(nn,2*nn)
    P = np.arange(2*nn,3*nn)

    # Assumption: divergence of lift is zero
    stokes_rhs = np.zeros((case.size,))
    stokes_rhs[V] -= case['laplacian-vv'](mu, lift=1)
    stokes_rhs[S] -= case['laplacian-sv'](mu, lift=1)

    mvv = case['laplacian-vv'](mu)
    msv = case['laplacian-sv'](mu)
    msp = case['divergence-sp'](mu)
    lhs = blocksolve(mvv, msv, msp, stokes_rhs, V, S, P)

    mvv += case['convection-vvv'](mu, lift=1) + case['convection-vvv'](mu, lift=2)
    msv += case['convection-svv'](mu, lift=1) + case['convection-svv'](mu, lift=2)

    stokes_rhs[V] -= case['convection-vvv'](mu, lift=(1,2))

    vmass = case.norm('v', 'h1s', mu=mu)

    for it in count(1):
        cc = case['convection-vvv']
        nvv = mvv + cc(mu, cont=(None, lhs[V], None)) + cc(mu, cont=(None, None, lhs[V]))
        cc = case['convection-svv']
        nsv = msv + cc(mu, cont=(None, lhs[V], None)) + cc(mu, cont=(None, None, lhs[V]))

        rhs = stokes_rhs.copy()
        rhs[V] -= mvv.matvec(lhs[V])
        rhs[S] -= msv.matvec(lhs[V])
        rhs[S] -= msp.matvec(lhs[P])
        rhs[V] -= case['convection-vvv'](mu, cont=(None,lhs[V],lhs[V]))
        rhs[S] -= case['convection-svv'](mu, cont=(None,lhs[V],lhs[V]))

        update = blocksolve(nvv, nsv, msp, rhs, V, S, P)
        lhs += update

        update_norm = np.sqrt(vmass.matvec(update).dot(update))
        log.info('update: {:.2e}'.format(update_norm))
        if update_norm < newton_tol:
            break

        if it > maxit:
            raise IterationCountError

    return lhs


def supremizer(case, mu, rhs):
    vinds, pinds = case.basis_indices(['v', 'p'])
    length = len(rhs)

    bmx = case['divergence'](mu).core[np.ix_(vinds,pinds)]
    rhs = bmx.dot(rhs[pinds])
    mx = matrix.ScipyMatrix(case['v-h1s'](mu).core[np.ix_(vinds,vinds)])

    lhs = mx.solve(rhs, constrain=case.cons[vinds])
    lhs.resize((length,))
    return lhs


def metrics(case, mu, lhs):
    domain = case.domain
    geom = case.physical_geometry(mu)
    vsol = case.solution(lhs, mu, 'v')

    area, div_norm = domain.integrate([1, vsol.div(geom) ** 2], geometry=geom, ischeme='gauss9')
    div_norm = np.sqrt(div_norm / area)

    log.user('velocity divergence: {:e}/area'.format(div_norm))
