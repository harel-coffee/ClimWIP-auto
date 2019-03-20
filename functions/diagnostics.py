#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Time-stamp: <2019-03-05 16:58:36 lukbrunn>

(c) 2018 under a MIT License (https://mit-license.org)

Authors:
- Ruth Lorenz || ruth.lorenz@env.ethz.ch
- Lukas Brunner || lukas.brunner@env.ethz.ch

Abstract:

"""
import os
import logging
import warnings
import regionmask
import numpy as np
import xarray as xr
from scipy import stats, signal
import matplotlib as mpl
mpl.use('Agg')  # need to set this here since salem imports plt
import salem
from cdo import Cdo
cdo = Cdo()

from utils_python.decorators import vectorize
from utils_python.xarray import standardize_dimensions

logger = logging.getLogger(__name__)
REGION_DIR = '{}/../cdo_data/'.format(os.path.dirname(__file__))
MASK = 'land_sea_mask_regionsmask.nc'  # 'seamask_g025.nc'
unit_save = None


@vectorize('(n)->(n)')
def detrend(data):
    if np.any(np.isnan(data)):
        return data * np.nan
    return signal.detrend(data)


@vectorize('(n)->()')
def trend(data):
    if np.any(np.isnan(data)):
        return np.nan
    xx = np.arange(len(data))
    return stats.linregress(xx, data).slope


def calculate_net_radiation(infile, varns, outname, diagn):
    assert varns == ('rlds', 'rlus', 'rsds', 'rsus')
    da1 = xr.open_dataset(infile, decode_cf=False)[varns[0]]
    da2 = xr.open_dataset(infile.replace(varns[0], varns[1]), decode_cf=False)[varns[1]]
    da3 = xr.open_dataset(infile.replace(varns[0], varns[2]), decode_cf=False)[varns[2]]
    da4 = xr.open_dataset(infile.replace(varns[0], varns[3]), decode_cf=False)[varns[3]]

    da = (da1-da2) + (da3-da4)
    da.attrs['units'] = da1.units
    try:
        da.attrs['_FillValue'] = da1._FillValue
    except AttributeError:
        da.attrs['_FillValue'] = 1e20
    da.attrs['long_name'] = 'Surface Downwelling Net Radiation'
    da.attrs['standard_name'] = 'surface_downwelling_net_flux_in_air'
    # TODO: units; positive direction definition as attrs
    ds = da.to_dataset(name=diagn)

    ds.to_netcdf(outname)


def standardize_units(da, varn):
    """Convert units to a common standard"""
    if 'units' in da.attrs.keys():
        unit = da.attrs['units']
        attrs = da.attrs
    else:
        logmsg = 'units attribute not found for {}'.format(varn)
        logger.warning(logmsg)
        return None

    # --- precipitation ---
    if varn == 'pr':
        newunit = 'mm/day'
        if unit == 'kg m-2 s-1':
            da.data *= 24*60*60
            da.attrs = attrs
            da.attrs['units'] = newunit
        elif unit == 'mm':  # E-OBS
            da.attrs['units'] = newunit
        elif unit == newunit:
            pass
        else:
            logmsg = 'Unit {} not covered for {}'.format(unit, varn)
            raise ValueError(logmsg)

    # --- temperature ---
    elif varn in ['tas', 'tasmax', 'tasmin', 'tos']:
        newunit = "degC"
        if unit == newunit:
            pass
        elif unit == 'K':
            da.data -= 273.15
            da.attrs = attrs
            da.attrs['units'] = newunit
        elif unit.lower() in ['degc', 'deg_c', 'celsius', 'degreec',
                              'degree_c', 'degree_celsius']:
            # https://ferret.pmel.noaa.gov/Ferret/documentation/udunits.dat
            da.attrs['units'] = newunit
        else:
            logmsg = 'Unit {} not covered for {}'.format(unit, varn)
            raise ValueError(logmsg)

    # --- pressure ---
    elif varn in ['psl']:
        newunit = 'pa'
        if unit.lower() == newunit:
            pass
        elif unit.lower() == 'hPa':
            da.data *= 100.
            da.attrs = attrs
            da.attrs['units'] = newunit
        else:
            logmsg = 'Unit {} not covered for {}'.format(unit, varn)
            raise ValueError(logmsg)

    # --- radiation ---
    elif varn in ['rsds', 'rsus', 'rlds', 'rlus', 'rnet']:
        newunit = 'W m**-2'
        if unit == newunit:
            pass
        elif unit == 'W m-2':
            da.attrs['units'] = newunit
        else:
            logmsg = 'Unit {} not covered for {}'.format(unit, varn)
            raise ValueError(logmsg)

    # --- not covered ---
    else:
        logmsg = 'Variable {} not covered in standardize_units'.format(varn)
        logger.warning(logmsg)

    return da


def calculate_basic_diagnostic(infile, varn,
                               outfile=None,
                               time_period=None,
                               season=None,
                               time_aggregation=None,
                               mask_ocean=False,
                               region='GLOBAL',
                               overwrite=False,
                               regrid=False):
    """
    Calculate a basic diagnostic from a given file.

    A basic diagnostic calculated from a input file by selecting a given
    region, time period, and season as well as applying a land-sea mask.
    Also, the time dimension is aggregated by different methods.

    Parameters
    ----------
    infile : str
        Full path of the input file. Must contain varn.
    varn : str
        The variable contained in infile.
    outfile : str
        Full path of the output file. Path must exist.
    time_period : tuple of two strings, optional
        Start and end of the time period. Both strings must be on of
        {"yyyy", "yyyy-mm", "yyyy-mm-dd"}.
    season : {'JJA', 'SON', 'DJF', 'MAM', 'ANN'}, optional
    time_aggregation : {'CLIM', 'STD', 'TREND'}, optional
        Type of time aggregation to use.
    mask_ocean : bool, optional
    region : list of strings or str, optional
        Each string must be a valid SREX region
    overwrite : bool, optional
        If True overwrite existing outfiles otherwise read and return them.
    regrid : bool, optional
        If True the file will be regridded by cdo.remapbil before opening.

    Returns
    -------
    diagnostic : xarray.DataArray
    """
    if not overwrite and outfile is not None and os.path.isfile(outfile):
        logger.debug('Diagnostic already exists & overwrite=False, skipping.')
        return xr.open_dataset(outfile)

    if regrid:
        infile = cdo.remapbil(os.path.join(REGION_DIR, MASK),
                              options='-b F64', input=infile)

    da = xr.open_dataset(infile)[varn]
    enc = da.encoding
    da = standardize_dimensions(da)
    assert np.all(da['lat'].data == np.arange(-88.75, 90., 2.5))
    assert np.all(da['lon'].data == np.arange(-178.75, 180., 2.5))

    if time_period is not None:
        da = da.sel(time=slice(str(time_period[0]), str(time_period[1])))

    if season in ['JJA', 'SON', 'DJF', 'MAM']:
        da = da.isel(time=da['time.season'] == season)
    elif season is None or season == 'ANN':
        pass
    else:
        raise NotImplementedError('season={}'.format(season))

    if region != 'GLOBAL':
        if (isinstance(region, str) and
            region not in regionmask.defined_regions.srex.abbrevs):
            # if region is not a SREX region read coordinate file
            regionfile = '{}.txt'.format(os.path.join(REGION_DIR, region))
            if not os.path.isfile(regionfile):
                raise ValueError(f'{regionfile} is not a valid regionfile')
            mask = np.loadtxt(regionfile)
            if mask.shape != (4, 2):
                errmsg = ' '.join([
                    f'Wrong file content for regionfile {regionfile}! Should',
                    'contain four lines with corners like: lon, lat'])
                raise ValueError(errmsg)
            lonmin, latmin = mask.min(axis=0)
            lonmax, latmax = mask.max(axis=0)
            if lonmax > 180 or lonmin < -180 or latmax > 90 or latmin < -90:
                raise ValueError(f'Wrong lat/lon value in {regionfile}')
            da = da.salem.roi(corners=((lonmin, latmin), (lonmax, latmax)))
            da = da.salem.subset(corners=((lonmin, latmin), (lonmax, latmax)), margin=1)
        else:
            if isinstance(region, str):
                region = [region]
            masks = []
            keys = regionmask.defined_regions.srex.map_keys(region)
            for key in keys:
                masks.append(
                    regionmask.defined_regions.srex.mask(da) == key)
            mask = sum(masks) == 1
            da = da.where(mask)
            da = da.salem.subset(roi=mask, margin=1)

        if np.all(np.isnan(da.isel(time=0))):
            errmsg = 'All grid points masked! Wrong masking settings?'
            logger.error(errmsg)
            raise ValueError(errmsg)

    if mask_ocean:
        sea_mask = regionmask.defined_regions.natural_earth.land_110.mask(da) == 0
        da = da.where(sea_mask)

    da = standardize_units(da, varn)
    attrs = da.attrs

    with warnings.catch_warnings():
        # grid cells outside the selected regions are filled with nan and will
        # prompt warning when .mean & .std are called.
        warnings.filterwarnings('ignore', message='Mean of empty slice')
        warnings.filterwarnings('ignore', message='Degrees of freedom <= 0 for slice')

        if time_aggregation == 'CLIM':
            da = da.groupby('time.year').mean('time', skipna=False)
            da = da.mean('year', skipna=False)
        elif time_aggregation == 'STD':
            da = xr.apply_ufunc(detrend, da,
                                input_core_dims=[['time']],
                                output_core_dims=[['time']],
                                keep_attrs=True)
            da = da.std('time', skipna=False)
        elif time_aggregation == 'TREND':
            da = xr.apply_ufunc(trend, da,
                                input_core_dims=[['time']],
                                output_core_dims=[[]],
                                keep_attrs=True)
            attrs['units'] = '{} year**-1'.format(attrs['units'])
        elif time_aggregation == 'CYC':
            da = da.groupby('time.month').mean('time')
        elif time_aggregation is None or time_aggregation == 'CORR':
            pass
        else:
            NotImplementedError(f'time_aggregation={time_aggregation}')

    ds = da.to_dataset(name=varn)
    ds[varn].attrs = attrs
    ds[varn].encoding = enc
    if outfile is not None:
        ds.to_netcdf(outfile)
    return ds


def calculate_diagnostic(infile, diagn, base_path, **kwargs):
    """
    Calculate basic or derived diagnostics depending on input.

    Parameters
    ----------
    infile : str
        Full path of the input file. Must contain exactly one non-dimension
        variable.
    diagn : str or dict
        * if str: diagn is assumed to also be a basic variable and will
          directly be used to call calculate_basic_diagnostic
        * if dict: diagn has to be exactly one key-value pair with the values
          representing basic variables and the key representing the name of
          the newly created diagnostic (e.g., {'tasclt': ['tas', clt']} will
          calculate the correlation between tas and clt.
    base_path : str
        The path in which to save the calculated diagnostic file.
    kwargs : dict
        Keyword arguments passed on to calculate_basic_diagnostic.

    Returns
    -------
    diagnostic : xarray.DataArray
    """
    def get_outfile(**kwargs):
        kwargs['infile'] = os.path.basename(kwargs['infile']).replace('.nc', '')
        if isinstance(kwargs['region'], list):
            kwargs['region'] = '-'.join(kwargs['region'])

        return os.path.join(base_path, '_'.join([
            '{infile}_{time_period[0]}-{time_period[1]}_{season}',
            '{time_aggregation}_{region}.nc']).format(**kwargs))

    @vectorize('(n),(n)->()')
    def _corr(arr1, arr2):
        return stats.pearsonr(arr1, arr2)[0]

    if isinstance(diagn, str):  # basic diagnostic
        outfile = get_outfile(infile=infile, **kwargs)
        return calculate_basic_diagnostic(infile, diagn, outfile, **kwargs)
    elif isinstance(diagn, dict):  # derived diagnostic
        diagn = dict(diagn)  # leave original alone (.pop!)
        assert len(diagn.keys()) == 1
        key = list(diagn.keys())[0]
        varns = diagn.pop(key)  # basic variables
        diagn = key  # derived diagnostic

        if diagn == 'rnet':
            tmpfile = os.path.join(
                base_path, os.path.basename(infile).replace(varns[0], diagn))
            calculate_net_radiation(infile, varns, tmpfile, diagn)
            outfile = get_outfile(infile=tmpfile, **kwargs)
            return calculate_basic_diagnostic(tmpfile, diagn, outfile, **kwargs)
        elif kwargs['time_aggregation'] == 'CORR':
            assert len(varns) == 2, 'can only correlate two variables'
            assert varns[0] != varns[1], 'can not correlate same variables'
            outfile1 = get_outfile(infile=infile, **kwargs)
            ds1 = calculate_basic_diagnostic(infile, varns[0], outfile1, **kwargs)

            # !! '.../...Datasets...'.replace('tas', 'pr') -> '.../...Daprets...' !!
            # !! '.../processed... -> .../tasocessed... !! (for obs)
            path, fn = os.path.split(infile)
            fn = fn.replace(f'{varns[0]}_', f'{varns[1]}_')
            path = (path+'/').replace(f'/{varns[0]}/', f'/{varns[1]}/')
            infile2 = os.path.join(path, fn)
            outfile2 = get_outfile(infile=infile2, **kwargs)
            ds2 = calculate_basic_diagnostic(infile2, varns[1], outfile2, **kwargs)
            da = xr.apply_ufunc(_corr, ds1[varns[0]], ds2[varns[1]],
                                input_core_dims=[['time'], ['time']])
            outfile3 = outfile1.replace(f'/{varns[0]}_', f'/{diagn}_')
            ds3 = da.to_dataset(name=diagn)
            ds3[diagn].attrs = {'units': '1'}
            ds3.to_netcdf(outfile3)
            return ds3
