import click
from functools import wraps
from itertools import count, product, repeat
import numpy as np
from nutils import plot, log, function as fn, _
from operator import itemgetter
from os.path import isfile, splitext
import pickle

import bbflow.cases as cases
from bbflow.cases.bases import Case
import bbflow.quadrature as quadrature
import bbflow.solvers as solvers


def parse_extra_args(func):
    @wraps(func)
    def inner(ctx, **kwargs):
        extra_args = {}
        args = ctx.args
        while args:
            key, args = args[0], args[1:]
            key = key[2:].replace('-', '_')
            values = ()
            while args and not args[0].startswith('--'):
                value, args = args[0], args[1:]
                for cons in [int, float]:
                    try:
                        value = cons(value)
                        break
                    except ValueError: pass
                values += (value,)
            if len(values) == 0:
                if key.startswith('no-'):
                    extra_args[key[3:]] = False
                else:
                    extra_args[key] = True
            elif len(values) == 1:
                extra_args[key] = values[0]
            else:
                extra_args[key] = values
        return func(ctx, **kwargs, **extra_args)
    return inner


class CaseType(click.ParamType):
    name = 'case'

    def convert(self, value, param, ctx):
        if isinstance(value, Case):
            return value
        elif value in cases.__dict__:
            return getattr(cases, value)
        elif isfile(value):
            with open(value, 'rb') as f:
                case = pickle.load(f)
            return lambda *args, **kwargs: case
        self.fail('Unknown case: {}'.format(value))


@click.group()
@click.pass_context
@click.option('--case', '-c', type=CaseType(), required=True)
@click.option('--solver', '-s', type=click.Choice(solvers.__all__), required=False)
def main(ctx, case, solver):
    ctx.obj = {
        'case': case,
        'solver': getattr(solvers, solver) if solver else None,
    }

def command(name=None):
    def decorator(func):
        func = click.pass_context(func)
        func = main.command(
            name,
            context_settings=dict(
                ignore_unknown_options=True,
                allow_extra_args=True,
            )
        )(func)
        return func
    return decorator


@command()
@parse_extra_args
def single(ctx, **kwargs):
    case = ctx.obj['case'](**kwargs)
    lhs = ctx.obj['solver'](case, **kwargs)
    solvers.metrics(case, lhs, **kwargs)
    solvers.plots(case, lhs, **kwargs)


def _make_ensemble(case, solver, imethod, ipts, **kwargs):
    scheme = list(getattr(quadrature, imethod)(case.mu, ipts))
    nsnapshots = len(scheme)
    log.info('Generating ensemble of {} snapshots'.format(nsnapshots))

    ensemble, params = [], []
    for mu, weight in log.iter('snapshot', scheme):
        log.info('mu = {}'.format(mu))
        ensemble.append(weight * solver(case, mu=mu, **kwargs))
        params.append(mu)
    ensemble = np.array(ensemble).T

    return ensemble, params


def _eigen(case, ensemble, fields):
    ret = {}
    for field in log.iter('field', fields, length=False):
        mass = case.mass(field)
        corr = ensemble.T.dot(mass.core.dot(ensemble))
        eigvals, eigvecs = np.linalg.eigh(corr)
        eigvals = eigvals[::-1] / sum(eigvals)
        eigvecs = eigvecs[:,::-1]
        ret[field] = (eigvals, eigvecs)

    return ret


def _reduction(case, ensemble, eigpairs, fields, num_modes):
    projection, lengths = [], []
    for field, nmodes in zip(fields, num_modes):
        eigvals, eigvecs = eigpairs[field]
        reduced = ensemble.dot(eigvecs[:,:nmodes]) / np.sqrt(eigvals[:nmodes])
        indices = case.basis_indices(field)
        mask = np.ones(reduced.shape[0], dtype=np.bool)
        mask[indices] = 0
        reduced[mask] = 0

        projection.append(reduced)
        lengths.append(nmodes)

    return np.concatenate(projection, axis=1), lengths


@command()
@click.option('--method', type=click.Choice(['pod']), default='pod')
@click.option('--imethod', type=click.Choice(['full', 'sparse']), default='full')
@click.option('--field', '-f', 'fields', type=str, multiple=True)
@click.option('--out', '-o', type=click.File(mode='wb'), required=True)
@parse_extra_args
@log.title
def reduce(ctx, out, fields, method, imethod, ipts=None, error=0.01, min_modes=None, **kwargs):
    case = ctx.obj['case'](**kwargs)
    ensemble, __ = _make_ensemble(case, ctx.obj['solver'], imethod, ipts, **kwargs)
    nsnapshots = ensemble.shape[1]
    fields = fields or case.fields

    if min_modes == -1:
        min_modes = nsnapshots

    eigpairs = _eigen(case, ensemble, fields)

    num_modes = []
    for field in fields:
        eigvals, __ = eigpairs[field]
        threshold = (1 - error ** 2) * sum(eigvals)
        try:
            nmodes = min(np.where(np.cumsum(eigvals) > threshold)[0]) + 1
            if min_modes:
                nmodes = max(nmodes, min_modes)
        except ValueError:
            nmodes = nsnapshots
        if nmodes == nsnapshots and min_modes != nsnapshots:
            log.warning('All DoFs used, ensemble is probably too small')
        actual_error = np.sqrt(np.sum(eigvals[nmodes:]) / sum(eigvals))
        log.info('{} modes suffice for {:.2e} error (threshold {:.2e})'.format(
            nmodes, actual_error, error,
        ))
        num_modes.append(nmodes)

    projection, lengths = _reduction(case, ensemble, eigpairs, fields, num_modes)

    tensors = False
    if hasattr(ctx.obj['solver'], 'needs_tensors'):
        tensors = ctx.obj['solver'].needs_tensors

    proj_case = cases.ProjectedCase(case, projection, fields, lengths, tensors=tensors)
    pickle.dump(proj_case, out)


@command('reduce-many')
@click.option('--method', type=click.Choice(['pod']), default='pod')
@click.option('--imethod', type=click.Choice(['full', 'sparse']), default='full')
@click.option('--field', '-f', 'fields', type=str, multiple=True)
@click.option('--out', '-o', required=True)
@parse_extra_args
@log.title
def reduce_many(ctx, out, fields, method, imethod, ipts=None, max_out=50, **kwargs):
    case = ctx.obj['case'](**kwargs)
    ensemble, __ = _make_ensemble(case, ctx.obj['solver'], imethod, ipts, **kwargs)
    nsnapshots = ensemble.shape[1]
    fields = fields or case.fields

    projection, lengths = [], []
    eigpairs = _eigen(case, ensemble, fields)
    all_eigvals = []

    for fieldid, field in enumerate(fields):
        eigvals, __ = eigpairs[field]
        eigvals /= sum(eigvals)
        all_eigvals.extend(zip(eigvals, repeat(fieldid)))

    all_eigvals = sorted(all_eigvals, key=itemgetter(0), reverse=True)
    num_modes = [0] * len(fields)
    errs = [1.0] * len(fields)
    for i, (ev, fieldid) in enumerate(all_eigvals):
        if i == max_out:
            break
        num_modes[fieldid] += 1
        errs[fieldid] -= ev
        if any(n == 0 for n in num_modes):
            continue
        projection, lengths = _reduction(case, ensemble, eigpairs, fields, num_modes)
        tensors = False
        if hasattr(ctx.obj['solver'], 'needs_tensors'):
            tensors = ctx.obj['solver'].needs_tensors
        proj_case = cases.ProjectedCase(case, projection, fields, lengths, tensors=tensors)
        proj_case.meta_errors = [np.sqrt(max(err,0)) for err in errs]
        proj_case.meta_nmodes = num_modes
        fn, ext = splitext(out)
        filename = '%s-%04d%s' % (fn, i, ext)
        with open(filename, 'wb') as f:
            pickle.dump(proj_case, f)


@command('plot-basis')
@parse_extra_args
@log.title
def plot_basis(ctx, mu, figsize=(10,10), colorbar=False, **kwargs):
    case = ctx.obj['case'](**kwargs)
    for field in case.fields:
        if field not in ['v', 'p']:
            continue
        basis = case.basis(field)

        bfuns = []
        for ind in case.basis_indices(field):
            coeffs = np.zeros((basis.shape[0],))
            coeffs[ind] = 1
            bfun = basis.dot(coeffs)
            bfuns.extend([bfun, fn.norm2(bfun)])

        geom = case.phys_geom(mu)
        points, *bfuns = case.domain.elem_eval([geom] + bfuns, ischeme='bezier9', separate=True)
        for num in log.count('bfun', start=1):
            if not bfuns:
                break
            velocity, speed, *bfuns = bfuns
            with plot.PyPlot(name='bfun_{}_'.format(field), index=num, figsize=figsize) as plt:
                plt.mesh(points, speed)
                if colorbar:
                    plt.colorbar()
                plt.streamplot(points, velocity, 0.1)


@command('analyze-error')
@parse_extra_args
@log.title
@click.option('--imethod', type=click.Choice(['full', 'sparse']), default='full')
def analyze_error(ctx, imethod, ipts=None, **kwargs):
    rcase = ctx.obj['case'](**kwargs)
    ocase = rcase.case
    solver = ctx.obj['solver']

    scheme = list(getattr(quadrature, imethod)(rcase.mu, ipts))
    ntrials = len(scheme)
    log.info('Sampling error in {} points'.format(ntrials))

    vmass = ocase.mass('v')

    max_error, total_error, total_weight = 0.0, 0.0, 0.0
    for mu, weight in log.iter('point', scheme):
        log.info('mu = {}'.format(mu))

        rlhs = rcase.solution_vector(solver(rcase, mu=mu, **kwargs))
        olhs = ocase.solution_vector(solver(ocase, mu=mu, **kwargs))
        diff = rlhs - olhs
        error = np.sqrt(vmass.dot(diff).dot(diff))
        log.info('Error = {:.2e}'.format(error))

        total_error += weight * error
        total_weight += weight
        max_error = max(max_error, error)

    total_error /= total_weight
    log.info('Mean error = {:.2e}'.format(total_error))
    log.info('Maximal error = {:.2e}'.format(max_error))


if __name__ == '__main__':
    main()
