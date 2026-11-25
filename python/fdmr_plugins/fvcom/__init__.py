"""FVCOM output as a 2D field (specification 7.3): NOAA's coastal and bay forecast
systems (San Francisco Bay's among them) publish FVCOM fields on a triangular
mesh. This plug-in reads them, cuts and coarsens the mesh for the compute
engine, and turns each hour into depth-averaged currents, water level,
temperature and salinity on the coarse mesh."""
