from builtins import zip
from builtins import range
# The base class for all spatial slicers.
# Slicers are 'data slicers' at heart; spatial slicers slice data by RA/Dec and
#  return the relevant indices in the simData to the metric.
# The primary things added here are the methods to slice the data (for any spatial slicer)
#  as this uses a KD-tree built on spatial (RA/Dec type) indexes.

import warnings
import numpy as np
from functools import wraps
from scipy.spatial import cKDTree as kdtree
from lsst.sims.maf.plots.spatialPlotters import BaseHistogram, BaseSkyMap

# For the footprint generation and conversion between galactic/equatorial coordinates.
from lsst.obs.lsstSim import LsstSimMapper
from lsst.sims.coordUtils import _chipNameFromRaDec
from lsst.sims.utils import ObservationMetaData

from .baseSlicer import BaseSlicer

__all__ = ['BaseSpatialSlicer']


class BaseSpatialSlicer(BaseSlicer):
    """Base spatial slicer object, contains additional functionality for spatial slicing,
    including setting up and traversing a kdtree containing the simulated data points.

    Parameters
    ----------
    lonCol : str, optional
        Name of the longitude (RA equivalent) column to use from the input data.
        Default fieldRA
    latCol : str, optional
        Name of the latitude (Dec equivalent) column to use from the input data.
        Default fieldDec
    verbose : boolean, optional
        Flag to indicate whether or not to write additional information to stdout during runtime.
        Default True.
    badval : float, optional
        Bad value flag, relevant for plotting. Default -666.
    leafsize : int, optional
        Leafsize value for kdtree. Default 100.
    radius : float, optional
        Radius for matching in the kdtree. Equivalent to the radius of the FOV. Degrees.
        Default 1.75.
    useCamera : boolean, optional
        Flag to indicate whether to use the LSST camera footprint or not.
        Default False.
    rotSkyPosColName : str, optional
        Name of the rotSkyPos column in the input  data. Only used if useCamera is True.
        Describes the orientation of the camera orientation compared to the sky.
        Default rotSkyPos.
    mjdColName : str, optional
        Name of the exposure time column. Only used if useCamera is True.
        Default observationStartMJD.
    chipNames : array-like, optional
        List of chips to accept, if useCamera is True. This lets users turn 'on' only a subset of chips.
        Default 'all' - this uses all chips in the camera.
    """
    def __init__(self, lonCol='fieldRA', latCol='fieldDec', verbose=True,
                 badval=-666, leafsize=100, radius=1.75, latLonDeg=True,
                 useCamera=False, rotSkyPosColName='rotSkyPos', mjdColName='observationStartMJD',
                 chipNames='all'):
        super(BaseSpatialSlicer, self).__init__(verbose=verbose, badval=badval)
        self.lonCol = lonCol
        self.latCol = latCol
        self.latLonDeg = latLonDeg
        self.rotSkyPosColName = rotSkyPosColName
        self.mjdColName = mjdColName
        self.columnsNeeded = [lonCol, latCol]
        self.useCamera = useCamera
        if useCamera:
            self.columnsNeeded.append(rotSkyPosColName)
            self.columnsNeeded.append(mjdColName)
        self.slicer_init = {'lonCol': lonCol, 'latCol': latCol,
                            'radius': radius, 'badval': badval,
                            'useCamera': useCamera}
        self.radius = radius
        self.leafsize = leafsize
        self.useCamera = useCamera
        self.chipsToUse = chipNames
        # RA and Dec are required slicePoint info for any spatial slicer. Slicepoint RA/Dec are in radians.
        self.slicePoints['sid'] = None
        self.slicePoints['ra'] = None
        self.slicePoints['dec'] = None
        self.nslice = None
        self.shape = None
        self.plotFuncs = [BaseHistogram, BaseSkyMap]

    def setupSlicer(self, simData, maps=None):
        """Use simData[self.lonCol] and simData[self.latCol] (in radians) to set up KDTree.

        Parameters
        -----------
        simData : numpy.recarray
            The simulated data, including the location of each pointing.
        maps : list of lsst.sims.maf.maps objects, optional
            List of maps (such as dust extinction) that will run to build up additional metadata at each
            slicePoint. This additional metadata is available to metrics via the slicePoint dictionary.
            Default None.
        """
        if maps is not None:
            if self.cacheSize != 0 and len(maps) > 0:
                warnings.warn('Warning:  Loading maps but cache on.'
                              'Should probably set useCache=False in slicer.')
            self._runMaps(maps)
        self._setRad(self.radius)
        if self.useCamera:
            self._setupLSSTCamera()
            self._presliceFootprint(simData)
        else:
            if self.latLonDeg:
                self._buildTree(np.radians(simData[self.lonCol]),
                                np.radians(simData[self.latCol]), self.leafsize)
            else:
                self._buildTree(simData[self.lonCol], simData[self.latCol], self.leafsize)

        @wraps(self._sliceSimData)
        def _sliceSimData(islice):
            """Return indexes for relevant opsim data at slicepoint
            (slicepoint=lonCol/latCol value .. usually ra/dec)."""

            # Build dict for slicePoint info
            slicePoint = {}
            if self.useCamera:
                indices = self.sliceLookup[islice]
                slicePoint['chipNames'] = self.chipNames[islice]
            else:
                sx, sy, sz = self._treexyz(self.slicePoints['ra'][islice],
                                           self.slicePoints['dec'][islice])
                # Query against tree.
                indices = self.opsimtree.query_ball_point((sx, sy, sz), self.rad)

            # Loop through all the slicePoint keys. If the first dimension of slicepoint[key] has
            # the same shape as the slicer, assume it is information per slicepoint.
            # Otherwise, pass the whole slicePoint[key] information. Useful for stellar LF maps
            # where we want to pass only the relevant LF and the bins that go with it.
            for key in self.slicePoints:
                if len(np.shape(self.slicePoints[key])) == 0:
                    keyShape = 0
                else:
                    keyShape = np.shape(self.slicePoints[key])[0]
                if (keyShape == self.nslice):
                    slicePoint[key] = self.slicePoints[key][islice]
                else:
                    slicePoint[key] = self.slicePoints[key]
            return {'idxs': indices, 'slicePoint': slicePoint}
        setattr(self, '_sliceSimData', _sliceSimData)

    def _setupLSSTCamera(self):
        """If we want to include the camera chip gaps, etc"""
        mapper = LsstSimMapper()
        self.camera = mapper.camera
        self.epoch = 2000.0

    def _presliceFootprint(self, simData):
        """Loop over each pointing and find which sky points are observed """
        # Now to make a list of lists for looking up the relevant observations at each slicepoint
        self.sliceLookup = [[] for dummy in range(self.nslice)]
        self.chipNames = [[] for dummy in range(self.nslice)]
        # Make a kdtree for the _slicepoints_
        # Using scipy 0.16 or later
        self._buildTree(self.slicePoints['ra'], self.slicePoints['dec'], leafsize=self.leafsize)

        # Loop over each unique pointing position
        if self.latLonDeg:
            lat = np.radians(simData[self.latCol])
            lon = np.radians(simData[self.lonCol])
        else:
            lat = simData[self.latCol]
            lon = simData[self.lonCol]
        for ind, ra, dec, rotSkyPos, mjd in zip(np.arange(simData.size), lon, lat,
                                                simData[self.rotSkyPosColName], simData[self.mjdColName]):
            dx, dy, dz = self._treexyz(ra, dec)
            # Find healpixels inside the FoV
            hpIndices = np.array(self.opsimtree.query_ball_point((dx, dy, dz), self.rad))
            if hpIndices.size > 0:
                obs_metadata = ObservationMetaData(pointingRA=np.degrees(ra),
                                                   pointingDec=np.degrees(dec),
                                                   rotSkyPos=np.degrees(rotSkyPos),
                                                   mjd=mjd)

                chipNames = _chipNameFromRaDec(self.slicePoints['ra'][hpIndices],
                                               self.slicePoints['dec'][hpIndices],
                                               epoch=self.epoch,
                                               camera=self.camera, obs_metadata=obs_metadata)
                # If we are using only a subset of chips
                if self.chipsToUse != 'all':
                    checkedChipNames = [chipName in self.chipsToUse for chipName in chipNames]
                    good = np.where(checkedChipNames)[0]
                    chipNames = chipNames[good]
                    hpIndices = hpIndices[good]
                # Find the healpixels that fell on a chip for this pointing
                good = np.where(chipNames != [None])[0]
                hpOnChip = hpIndices[good]
                for i, chipName in zip(hpOnChip, chipNames[good]):
                    self.sliceLookup[i].append(ind)
                    self.chipNames[i].append(chipName)

        if self.verbose:
            "Created lookup table after checking for chip gaps."

    def _treexyz(self, ra, dec):
        """Calculate x/y/z values for ra/dec points, ra/dec in radians."""
        # Note ra/dec can be arrays.
        x = np.cos(dec) * np.cos(ra)
        y = np.cos(dec) * np.sin(ra)
        z = np.sin(dec)
        return x, y, z

    def _buildTree(self, simDataRa, simDataDec, leafsize=100):
        """Build KD tree on simDataRA/Dec and set radius (via setRad) for matching.

        simDataRA, simDataDec = RA and Dec values (in radians).
        leafsize = the number of Ra/Dec pointings in each leaf node."""
        if np.any(np.abs(simDataRa) > np.pi*2.0) or np.any(np.abs(simDataDec) > np.pi*2.0):
            raise ValueError('Expecting RA and Dec values to be in radians.')
        x, y, z = self._treexyz(simDataRa, simDataDec)
        data = list(zip(x, y, z))
        if np.size(data) > 0:
            try:
                self.opsimtree = kdtree(data, leafsize=leafsize, balanced_tree=False, compact_nodes=False)
            except TypeError:
                self.opsimtree = kdtree(data, leafsize=leafsize)
        else:
            raise ValueError('SimDataRA and Dec should have length greater than 0.')

    def _setRad(self, radius=1.75):
        """Set radius (in degrees) for kdtree search.

        kdtree queries will return pointings within rad."""
        x0, y0, z0 = (1, 0, 0)
        x1, y1, z1 = self._treexyz(np.radians(radius), 0)
        self.rad = np.sqrt((x1-x0)**2+(y1-y0)**2+(z1-z0)**2)
