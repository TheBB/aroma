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


from contextlib import contextmanager
import numpy as np
import nutils.plot
from nutils import function as fn

from nutils import function as fn


@contextmanager
def _plot(suffix, name='solution', figsize=(10,10), index=None, lines=True, mesh=None,
          xlim=None, ylim=None, axes=True, show=False, **kwargs):
    ndigits = 0 if index is None else 3
    with nutils.plot.PyPlot(f'{name}-{suffix}', figsize=figsize, index=index, ndigits=ndigits) as plt:
        yield plt
        if mesh: plt.segments(mesh, linewidth=0.1, color='black')
        plt.aspect('equal')
        plt.autoscale(enable=True, axis='both', tight=True)
        if xlim: plt.xlim(*xlim)
        if ylim: plt.ylim(*ylim)
        if not axes: plt.axis('off')
        if show: plt.show()


def _colorbar(plt, clim=None, colorbar=False, **kwargs):
    if clim: plt.clim(*clim)
    if colorbar: plt.colorbar()


def velocity(case, mu, lhs, density=1, **kwargs):
    tri, mesh = case.triangulation(mu, lines=True)
    vvals = case.solution(lhs, 'v', mu)
    vnorm = np.linalg.norm(vvals, axis=-1)

    with _plot('v', mesh=mesh, **kwargs) as plt:
        plt.tripcolor(tri, vnorm, shading='gouraud')
        _colorbar(plt, **kwargs)
        plt.streamplot(tri, vvals, spacing=0.1, density=density, color='black')


def pressure(case, mu, lhs, **kwargs):
    tri, mesh = case.triangulation(mu, lines=True)
    pvals = case.solution(lhs, 'p', mu)

    with _plot('p', mesh=mesh, **kwargs) as plt:
        plt.tripcolor(tri, pvals, shading='gouraud')
        _colorbar(plt, **kwargs)


def deformation(case, mu, lhs, stress='xx', **kwargs):
    disp = case.basis('u').obj.dot(lhs)
    geom = case.geometry + disp

    E = mu['ymod']
    NU = mu['prat']
    MU = E / (1 + NU)
    LAMBDA = E * NU / (1 + NU) / (1 - 2*NU)
    stressfunc = - MU * disp.symgrad(case.geometry) + LAMBDA * disp.div(case.geometry) * fn.eye(disp.shape[0])

    if stress == 'xx':
        stressfunc = stressfunc[0,0]
    elif stress == 'xy' or stress == 'yx':
        stressfunc = stressfunc[0,1]
    else:
        stressfunc = stressfunc[1,1]

    mesh, stressdata = case.domain.elem_eval([geom, stressfunc], separate=True, ischeme='bezier3')

    with _plot(f'u-{stress}', **kwargs) as plt:
        plt.mesh(mesh, stressdata)
        _colorbar(plt, **kwargs)
