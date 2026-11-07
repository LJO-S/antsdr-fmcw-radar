import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import chi2
from kf import Target, Radar, CvKalmanFilter, cross_check

# Simulation parameters
SEED = 42
DT, TOTAL_TIME = 0.04, 60.0
SR, SV, SA_TRUE = 0.7, 0.5, 1.0

# NIS of a consistent filter is chi-square with dof = measurement dimension (r, v)
NIS_DOF = 2
NIS_LO, NIS_HI = chi2.ppf([0.025, 0.975], NIS_DOF)  # single-sample 95 % band
