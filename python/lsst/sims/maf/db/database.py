from __future__ import print_function
from future.utils import with_metaclass
import os
import inspect
import numpy as np
from sqlalchemy import func, text
from sqlalchemy import Table
from sqlalchemy.engine import reflection
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", UserWarning)
    from lsst.sims.catalogs.db import DBObject


__all__ = ['DatabaseRegistry', 'Database']

class DatabaseRegistry(type):
    """
    Meta class for databases, to build a registry of database classes.
    """
    def __init__(cls, name, bases, dict):
        super(DatabaseRegistry, cls).__init__(name, bases, dict)
        if not hasattr(cls, 'registry'):
            cls.registry = {}
        modname = inspect.getmodule(cls).__name__ + '.'
        if modname.startswith('lsst.sims.maf.db'):
            modname = ''
        else:
            if len(modname.split('.')) > 1:
                modname = '.'.join(modname.split('.')[:-1]) + '.'
            else:
                modname = modname + '.'
        databasename = modname + name
        if databasename in cls.registry:
            raise Exception('Redefining databases %s! (there are >1 database classes with the same name)'
                            %(databasename))
        if databasename not in ['BaseDatabase']:
            cls.registry[databasename] = cls
    def getClass(cls, databasename):
        return cls.registry[databasename]
    def help(cls, doc=False):
        for databasename in sorted(cls.registry):
            if not doc:
                print(databasename)
            if doc:
                print('---- ', databasename, ' ----')
                print(inspect.getdoc(cls.registry[databasename]))


class Database(with_metaclass(DatabaseRegistry, DBObject)):
    """Base class for database access. Implements some basic query functionality and demonstrates API.

    Parameters
    ----------
    database : str
        Name of the database (or full path + filename for sqlite db).
    driver : str, opt
        Dialect+driver for sqlalchemy. Default 'sqlite'. (other examples, 'pymssql+mssql').
    host : str, opt
        Hostname for database. Default None (for sqlite).
    port : int, opt
        Port for database. Default None.
    defaultTable : str, opt
        Default table in the database to query for metric data.
    longstrings : bool, opt
        Flag to convert strings in database to long (1024) or short (256) characters in numpy recarray.
        Default False (convert to 256 character strings).
    verbose : bool, opt
        Flag for additional output. Default False.
    """

    def __init__(self, database, driver='sqlite', host=None, port=None, defaultTable=None,
                 longstrings=False, verbose=False):
        # If it's a sqlite file, check that the filename exists.
        # This gives a more understandable error message than trying to connect to non-existent file later.
        if driver=='sqlite':
            if not os.path.isfile(database):
                raise IOError('Sqlite database file "%s" not found.' %(database))

        # Connect to database using DBObject init.
        super(Database, self).__init__(database=database, driver=driver,
                                       host=host, port=port, verbose=verbose, connection=None)

        self.dbTypeMap = {'BIGINT': (int,), 'BOOLEAN': (bool,), 'FLOAT': (float,), 'INTEGER': (int,),
                          'NUMERIC': (float,), 'SMALLINT': (int,), 'TINYINT': (int,),
                          'VARCHAR': (np.str, 256), 'TEXT': (np.str, 256), 'CLOB': (np.str, 256),
                          'NVARCHAR': (np.str, 256), 'NCLOB': (np.str, 256), 'NTEXT': (np.str, 256),
                          'CHAR': (np.str, 1), 'INT': (int,), 'REAL': (float,), 'DOUBLE': (float,),
                          'STRING': (np.str, 256), 'DOUBLE_PRECISION': (float,), 'DECIMAL': (float,),
                          'DATETIME': (np.str, 50)}
        if longstrings:
            typeOverRide = {'VARCHAR':(np.str, 1024), 'NVARCHAR':(np.str, 1024),
                            'TEXT':(np.str, 1024), 'CLOB':(np.str, 1024),
                            'STRING':(np.str, 1024)}
            self.dbTypeMap.update(typeOverRide)

        # Get a dict (keyed by the table names) of all the columns in each table and view.
        self.tableNames = reflection.Inspector.from_engine(self.connection.engine).get_table_names()
        self.tableNames += reflection.Inspector.from_engine(self.connection.engine).get_view_names()
        self.columnNames = {}
        for t in self.tableNames:
            cols = reflection.Inspector.from_engine(self.connection.engine).get_columns(t)
            self.columnNames[t] = [xxx['name'] for xxx in cols]
        # Create all the sqlalchemy table objects. This lets us see the schema and query it with types.
        self.tables = {}
        for tablename in self.tableNames:
            self.tables[tablename] = Table(tablename, self.connection.metadata, autoload=True)
        self.defaultTable = defaultTable
        # if there is is only one table and we haven't said otherwise, set defaultTable automatically.
        if self.defaultTable is None and len(self.tableNames) == 1:
            self.defaultTable = self.tableNames[0]

    def close(self):
        self.connection.session.close()
        self.connection.engine.dispose()

    def fetchMetricData(self, colnames, sqlconstraint=None, groupBy=None, tableName=None):
        """Fetch 'colnames' from 'tableName'.

        This is basically a thin wrapper around query_columns, but uses the default table.
        It's mostly still here for backward compatibility.

        Parameters
        ----------
        colnames : list
            The columns to fetch from the table.
        sqlconstraint : str or None, opt
            The sql constraint to apply to the data (minus "WHERE"). Default None.
            Examples: to fetch data for the r band filter only, set sqlconstraint to 'filter = "r"'.
        groupBy : str or None, opt
            The column to group the returned data by.
            Default (when using summaryTable) is the MJD, otherwise will be None.
        tableName : str or None, opt
            The table to query. The default (None) will use the summary table, set by self.defaultTable.

        Returns
        -------
        np.recarray
            A structured array containing the data queried from the database.
        """
        if tableName is None:
            tableName = self.defaultTable

        # For a basic Database object, there is no default column to group by. So reset to None.
        if groupBy == 'default':
            groupBy = None

        if tableName not in self.tableNames:
            raise ValueError('Table %s not recognized; not in list of database tables.' % (tableName))

        metricdata = self.query_columns(tableName, colnames=colnames, sqlconstraint=sqlconstraint,
                                        groupBy=groupBy)
        return metricdata

    def fetchConfig(self, *args, **kwargs):
        """Get config (metadata) info on source of data for metric calculation.
        """
        # Demo API (for interface with driver).
        configSummary = {}
        configDetails = {}
        return configSummary, configDetails

    def query_arbitrary(self, sqlQuery, dtype=None):
        """Simple wrapper around execute_arbitrary for backwards compatibility.

        Parameters
        -----------
        sqlQuery : str
            SQL query.
        dtype: opt, numpy dtype.
            Numpy recarray dtype. If None, then an attempt to determine the dtype will be made.
            This attempt will fail if there are commas in the data you query.

        Returns
        -------
        numpy.recarray
        """
        return self.execute_arbitrary(sqlQuery, dtype=dtype)

    def query_columns(self, tablename, colnames=None, sqlconstraint=None,
                            groupBy=None, numLimit=None, chunksize=1000000):
        """Query a table in the database and return data from colnames in recarray.

        Parameters
        ----------
        tablename : str
            Name of table to query.
        colnames : list of str or None, opt
            Columns from the table to query for. If None, all columns are selected.
        sqlconstraint : str or None, opt
            Constraint to apply to to the query.  Default None.
        groupBy : str or None, opt
            Name of column to group by. Default None.
        numLimit : int or None, opt
            Number of records to return. Default no limit.
        chunksize : int, opt
            Query database and convert to recarray in series of chunks of chunksize.

        Returns
        -------
        numpy.recarray
        """
        # Build the sqlalchemy query from a single table, with various columns/constraints/etc.
        # Does NOT use a mapping between column names and database names - assumes the database names
        # are what the user will specify.

        # Build the query.
        query = self._build_query(tablename, colnames=colnames, sqlconstraint=sqlconstraint,
                                  groupBy=groupBy, numLimit=numLimit)

        # Determine dtype for numpy recarray.
        dtype = []
        for col in colnames:
            dt = self.dbTypeMap[self.tables[tablename].c[col].type.__visit_name__]
            dtype.append((col,) + dt)

        # Execute query on database.
        exec_query = self.connection.session.execute(query)

        if chunksize is None or chunksize==0:
            # Fetch all results and convert to numpy recarray.
            results = exec_query.fetchall()
            data = self._convert_results(results, dtype)
        else:
            chunks = []
            # Loop through results, converting in steps of chunksize.
            results = exec_query.fetchmany(chunksize)
            while len(results) > 0:
                chunks.append(self._convert_results(results, dtype))
                results = exec_query.fetchmany(chunksize)
            if len(chunks) == 0:
                data = np.recarray((0,), dtype=dtype)
            else:
                data = np.hstack(chunks)
        return data

    def _build_query(self, tablename, colnames, sqlconstraint=None, groupBy=None, numLimit=None):
        if tablename not in self.tables:
            raise ValueError('Tablename %s not in list of available tables (%s).'
                             % (tablename, self.tables.keys()))
        if colnames is None:
            colnames = self.columnNames[tablename]
        else:
            for col in colnames:
                if col not in self.columnNames[tablename]:
                    raise ValueError("Requested column %s not available in table %s" % (col, tablename))
            if groupBy is not None:
                if groupBy not in self.columnNames[tablename]:
                    raise ValueError("GroupBy column %s is not available in table %s" % (groupBy, tablename))
        # Put together sqlalchemy query object.
        for col in colnames:
            if col == colnames[0]:
                query = self.connection.session.query(col)
            else:
                query = query.add_columns(col)
        query = query.select_from(self.tables[tablename])
        if sqlconstraint is not None:
            if len(sqlconstraint) > 0:
                query = query.filter(text(sqlconstraint))
        if groupBy is not None:
            query = query.group_by(groupBy)
        if numLimit is not None:
            query = query.limit(numLimit)
        return query

    def _convert_results(self, results, dtype):
        if len(results) == 0:
            data = np.recarray((0,), dtype=dtype)
        else:
            # Have to do the tuple(xx) for py2 string objects. With py3 is okay to just pass results.
            data = np.rec.fromrecords([tuple(xx) for xx in results], dtype=dtype)
        return data