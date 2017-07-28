import numpy as np
from nutils import mesh, function as fn, log, _

from bbflow.cases.bases import mu, Case, num_elems


class backstep(Case):

    mu = [
        (20, 50),               # inverse of viscosity
        (9, 12),                # channel length
        (0.3, 2),               # step height
        (0.5, 1.2),             # inlet velocity
    ]
    _std_mu = (1.0, 10.0, 1.0, 1.0)
    fields = ['v', 'p']

    def __init__(self,
                 nel_length=100, nel_height=10, nel_width=None, nel_up=None,
                 meshwidth=0.1, degree=3, **kwargs):

        nel_width = num_elems(1.0, meshwidth, nel_width)
        nel_up = num_elems(1.0, meshwidth, nel_up)

        # Three-patch domain
        domain, geom = mesh.multipatch(
            patches=[[[0,1],[3,4]], [[3,4],[6,7]], [[2,3],[5,6]]],
            nelems={
                (0,1): nel_up, (3,4): nel_up, (6,7): nel_up,
                (2,5): nel_length, (3,6): nel_length, (4,7): nel_length,
                (0,3): nel_width, (1,4): nel_width,
                (2,3): nel_height, (5,6): nel_height,
            },
            patchverts=[
                [-1, 0], [-1, 1],
                [0, -1], [0, 0], [0, 1],
                [1, -1], [1, 0], [1, 1]
            ],
        )

        # Bases
        bases = [
            domain.basis('spline', degree=(degree, degree-1)),
            domain.basis('spline', degree=(degree-1, degree)),
            domain.basis('spline', degree=degree-1),
        ]
        vxbasis, vybasis, pbasis = fn.chain(bases)
        vbasis = vxbasis[:,_] * (1,0) + vybasis[:,_] * (0,1)

        basis_lengths = [len(bases[0]) + len(bases[1]), len(bases[2])]
        super().__init__(domain, geom, [vbasis, pbasis], basis_lengths)

        vgrad = vbasis.grad(geom)

        # Dirichlet boundary constraints
        if not hasattr(self, 'constraints'):
            boundary = domain.boundary[','.join([
                'patch0-bottom', 'patch0-top', 'patch0-left',
                'patch1-top', 'patch2-bottom', 'patch2-left',
            ])]
            constraints = boundary.project((0, 0), onto=vbasis, geometry=geom, ischeme='gauss9')
            self.constraints = constraints

        # Lifting function
        with self.add_lift() as add:
            x, y = geom
            profile = fn.max(0, y*(1-y) * 4)[_] * (1, 0)
            lift = domain.project(profile, onto=vbasis, geometry=geom, ischeme='gauss9')
            add(lift, scale=mu[3])

        # Stokes divergence term
        with self.add_matrix('divergence', rhs=True) as add:
            add(-fn.outer(vbasis.div(geom), pbasis), symmetric=True)
            add(-fn.outer(vgrad[:,0,0], pbasis), mu[2] - 1, domain=2, symmetric=True)
            add(-fn.outer(vgrad[:,1,1], pbasis), mu[1] - 1, domain=(1,2), symmetric=True)

        # Stokes laplacian term
        with self.add_matrix('laplacian', rhs=True) as add:
            add(fn.outer(vgrad).sum([-1, -2]), 1 / mu[0], domain=0)
            add(fn.outer(vgrad[:,:,0]).sum(-1), 1 / mu[0] / mu[1], domain=1)
            add(fn.outer(vgrad[:,:,1]).sum(-1), mu[1] / mu[0], domain=1)
            add(fn.outer(vgrad[:,:,0]).sum(-1), mu[2] / mu[0] / mu[1], domain=2)
            add(fn.outer(vgrad[:,:,1]).sum(-1), mu[1] / mu[0] / mu[2], domain=2)

        # Navier-stokes convective term
        with self.add_matrix('convection', rhs=(1,2)) as add:
            itg = (vbasis[:,_,_,:,_] * vbasis[_,:,_,_,:] * vgrad[_,_,:,:,:]).sum([-1, -2])
            add(itg, domain=0)
            itg = (vbasis[:,_,_,:] * vbasis[_,:,_,_,0] * vgrad[_,_,:,:,0]).sum(-1)
            add(itg, domain=1)
            add(itg, mu[2], domain=2)
            itg = (vbasis[:,_,_,:] * vbasis[_,:,_,_,1] * vgrad[_,_,:,:,1]).sum(-1)
            add(itg, mu[1], domain=(1,2))

    def phys_geom(self, p=None):
        if p is None:
            p = self.std_mu()
        x, y = self.geom
        xscale = 1.0 + (p[1] - 1) * fn.heaviside(x)
        yscale = 1.0 + (p[2] - 1) * fn.heaviside(-y)
        return self.geom * (xscale, yscale)
