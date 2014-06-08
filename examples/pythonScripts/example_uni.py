## EXAMPLE
# example test script for unibinner metrics. 


import sys, os, argparse
import numpy as np
import matplotlib.pyplot as plt
import lsst.sims.maf.db as db
import lsst.sims.maf.binners as binners
import lsst.sims.maf.metrics as metrics
import lsst.sims.maf.binMetrics as binMetrics
import lsst.sims.maf.db as db

import time
def dtime(time_prev):
   return (time.time() - time_prev, time.time())


def getMetrics():
    t = time.time()
    # Set up metrics.
    metricList = []
    # Simple metrics: 
    metricList.append(metrics.MeanMetric('finSeeing'))
    metricList.append(metrics.RmsMetric('finSeeing'))
    metricList.append(metrics.MedianMetric('airmass'))
    metricList.append(metrics.RmsMetric('airmass'))
    metricList.append(metrics.MeanMetric('fivesigma_modified'))
    metricList.append(metrics.RmsMetric('fivesigma_modified'))
    metricList.append(metrics.MeanMetric('skybrightness_modified'))
    metricList.append(metrics.CountMetric('expMJD'))
    dt, t = dtime(t)
    print 'Set up metrics %f s' %(dt)
    return metricList

def getBinner(simdata):
    t = time.time()
    bb = binners.UniBinner()
    bb.setupBinner(simdata)
    
    dt, t = dtime(t)
    print 'Set up binner %f s' %(dt)
    return bb

def goBin(opsimrun, metadata, simdata, bb, metricList):
    t = time.time()
    gm = binMetrics.BaseBinMetric()
    gm.setBinner(bb)

    gm.setMetrics(metricList)
    gm.runBins(simdata, simDataName=opsimrun, metadata=metadata)
    dt, t = dtime(t)
    print 'Ran bins of %d points with %d metrics using binMetric %f s' %(len(bb), len(metricList), dt)
                    
    gm.reduceAll()
    
    dt, t = dtime(t)
    print 'Ran reduce functions %f s' %(dt)

    return gm


def write(gm):
    t= time.time()
    gm.writeAll()
    dt, t = dtime(t)
    print 'Wrote outputs %f s' %(dt)

def printSummary(gm, metricList):
    t = time.time()
    for m in metricList:
        try:
            value = gm.computeSummaryStatistics(m.name, metrics.MeanMetric(''))
            print 'Summary for', m.name, ':', value
        except ValueError:
            pass
    dt, t = dtime(t)
    print 'Computed summaries %f s' %(dt)

    
if __name__ == '__main__':
    
    # Parse command line arguments for database connection info.
    parser = argparse.ArgumentParser()
    parser.add_argument("simDataTable", type=str, help="Filename (with path) of sqlite database")
    parser.add_argument("--sqlConstraint", type=str, default="filter='r'",
                        help="SQL constraint, such as filter='r' or propID=182")
    args = parser.parse_args()

    # Get db connection info.                                                                                                                        
    dbAddress = 'sqlite:///' + args.simDataTable
    oo = db.OpsimDatabase(dbAddress)

    opsimrun = oo.fetchOpsimRunName()

    sqlconstraint = args.sqlConstraint
    
    # Set up metrics. 
    metricList = getMetrics()

    # Find columns that are required.
    colnames = list(metricList[0].classRegistry.uniqueCols())
    
    # Get opsim simulation data
    simdata = oo.fetchMetricData(colnames, sqlconstraint)
    
    # And set up binner.
    bb = getBinner(simdata)
    
    # Okay, go calculate the metrics.
    metadata = sqlconstraint.replace('=','').replace('filter','').replace("'",'').replace('"', '')
    gm = goBin(opsimrun, metadata, simdata, bb, metricList)

    # Generate some summary statistics and plots.
    printSummary(gm, metricList)

    # No plots for unibinner (these are single number results).
    
    # Write the data to file.
    write(gm)
    