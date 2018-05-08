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
from nutils import log
import scipy.sparse as sp
import scipy.sparse._sparsetools as sptools


class CSRAssembler:

    def __init__(self, shape, row, col):
        assert len(shape) == 2
        assert np.max(row) < shape[0]
        assert np.max(col) < shape[1]

        order = np.lexsort((row, col))
        row, col = row[order], col[order]
        mask = ((row[1:] != row[:-1]) | (col[1:] != col[:-1]))
        mask = np.append(True, mask)
        row, col = row[mask], col[mask]
        inds, = np.nonzero(mask)

        M, N = shape
        idx_dtype = sp.sputils.get_index_dtype((row, col), maxval=max(len(row), N))
        self.row = row.astype(idx_dtype, copy=False)
        self.col = col.astype(idx_dtype, copy=False)

        self.order, self.inds = order, inds
        self.shape = shape

    def write(self, group):
        to_dataset(self.row, group, 'row')
        to_dataset(self.col, group, 'col')
        to_dataset(self.order, group, 'order')
        to_dataset(self.inds, group, 'inds')
        group.attrs['shape'] = self.shape
        group.attrs['type'] = 'CSRAssembler'

    @staticmethod
    def read(group):
        retval = CSRAssembler.__new__(CSRAssembler)
        retval.row = group['row'][:]
        retval.col = group['col'][:]
        retval.order = group['order'][:]
        retval.inds = group['inds'][:]
        retval.shape = tuple(group.attrs['shape'])
        return retval

    def __call__(self, data):
        data = np.add.reduceat(data[self.order], self.inds)

        M, N = self.shape
        indptr = np.empty(M+1, dtype=self.row.dtype)
        indices = np.empty_like(self.col, dtype=self.row.dtype)
        new_data = np.empty_like(data)

        sptools.coo_tocsr(M, N, len(self.row), self.row, self.col, data, indptr, indices, new_data)
        return sp.csr_matrix((new_data, indices, indptr), shape=self.shape)

    def ensure_shareable(self):
        self.row, self.col, self.order, self.inds = map(
            shared_array, (self.row, self.col, self.order, self.inds)
        )


class VectorAssembler:

    def __init__(self, shape, inds):
        assert len(shape) == 1
        assert np.max(inds) < shape[0]

        order = np.lexsort((inds,))
        inds = inds[order]
        mask = inds[1:] != inds[:-1]
        mask = np.append(True, mask)
        self.row = inds[mask]
        inds, = np.nonzero(mask)

        self.order, self.inds = order, inds
        self.shape = shape

    def write(self, group):
        to_dataset(self.row, group, 'row')
        to_dataset(self.order, group, 'order')
        to_dataset(self.inds, group, 'inds')
        group.attrs['shape'] = self.shape
        group.attrs['type'] = 'VectorAssembler'

    @staticmethod
    def read(group):
        retval = VectorAssembler.__new__(VectorAssembler)
        retval.row = group['row'][:]
        retval.order = group['order'][:]
        retval.inds = group['inds'][:]
        retval.shape = tuple(group.attrs['shape'])
        return retval

    def __call__(self, data):
        data = np.add.reduceat(data[self.order], self.inds)
        retval = np.zeros(self.shape, dtype=data.dtype)
        retval[self.row] = data
        return retval

    def ensure_shareable(self):
        self.row, self.order, self.inds = map(shared_array, (self.row, self.order, self.inds))


class SparseArray:

    def __init__(self, data, indices, shape):
        assert len(shape) == len(indices)
        self.indices = indices
        self.data = data
        self.shape = shape
        self.ndim = len(shape)

    @property
    def T(self):
        return SparseArray(self.data, self.indices[::-1], self.shape[::-1])

    def __add__(self, other):
        assert isinstance(other, SparseArray)
        assert other.shape == self.shape
        assert other.ndim == 2
        coo = (self.export('coo') + other.export('coo')).tocoo()
        return SparseArray(coo.data, (coo.row, coo.col), coo.shape)

    def contract(self, contraction):
        remaining = []
        data = np.copy(self.data)
        for i, c in enumerate(contraction):
            if c is None:
                remaining.append(i)
            else:
                data *= c[self.indices[i]]
        return SparseArray(data, tuple(self.indices[i] for i in remaining), tuple(self.shape[i] for i in remaining))

    def export(self, format='csr'):
        if format == 'csr' and self.ndim == 2:
            return sp.csr_matrix((self.data, self.indices), shape=self.shape)
        if format == 'coo' and self.ndim == 2:
            return sp.coo_matrix((self.data, self.indices), shape=self.shape)
        if format == 'dense' and self.ndim == 0:
            return np.sum(self.data)
        if format == 'dense' and self.ndim == 1:
            col = np.zeros_like(self.indices[0])
            matrix = sp.coo_matrix((self.data, (self.indices[0], col)), shape=self.shape + (1,))
            return matrix.toarray().reshape(self.shape)
        if format == 'dense' and self.ndim == 3:
            flat_index = np.ravel_multi_index(self.indices[1:], self.shape[1:])
            flat_shape = (self.shape[0], np.product(self.shape[1:]))
            matrix = sp.coo_matrix((self.data, (self.indices[0], flat_index)), shape=flat_shape)
            matrix = matrix.toarray()
            return np.reshape(matrix, self.shape)
        raise NotImplementedError(f'{self.ndim} {format}')

    def project(self, projection):
        # TODO: Remove this condition
        assert all(p is not None for p in projection)

        if self.ndim == 3:
            pa, pb, pc = projection
            P, __ = pa.shape
            ass = CSRAssembler(self.shape[1:], self.indices[1], self.indices[2])
            ret = np.empty((P, pb.shape[0], pc.shape[0]), self.data.dtype)
            for i in log.iter('index', range(P), length=P):
                data = self.data * pa[i, self.indices[0]]
                mx = ass(data)
                ret[i] = pb.dot(mx.dot(pc.T))
            return ret

        if self.ndim == 2:
            pa, pb = projection
            obj = self.export('csr')
            return pa.dot(obj.dot(pb.T))
