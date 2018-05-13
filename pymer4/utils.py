from __future__ import division

__all__  = ['get_resource_path',
            'discrete_inverse_logit',
            '_sig_stars',
            '_robust_estimator',
            '_chunk_boot_ols_coefs',
            '_chunk_perm_ols',
            '_ols',
            '_perm_find']

__author__ = ['Eshin Jolly']
__license__ = "MIT"

from os.path import dirname,join, sep
import numpy as np
import pandas as pd
from patsy import dmatrices
from scipy.special import expit
from rpy2.robjects.packages import importr
from rpy2.robjects import pandas2ri

base = importr('base')

def get_resource_path():
    """ Get path sample data directory. """
    return join(dirname(__file__), 'resources') + sep

def discrete_inverse_logit(arr):
    """ Apply a discretized inverse logit transform to an array of values. Useful for converting normally distributed values to binomial classes"""
    probabilities = expit(arr)
    out = np.random.binomial(1, probabilities)
    return out

def _sig_stars(val):
    """Adds sig stars to coef table prettier output."""
    star = ''
    if 0 <= val < .001:
        star = '***'
    elif .001 <= val < 0.01:
        star = '**'
    elif .01 <= val < .05:
        star = '*'
    elif .05 <= val < .1:
        star = '.'
    return star

def _robust_estimator(vals,X,robust_estimator='hc0',n_lags=1,cluster=None):
    """
    Computes robust sandwich estimators for standard errors used in OLS computation. Types include:
    'hc0': Huber (1980) sandwich estimator to return robust standard error estimates.
    'hc3': MacKinnon and White (1985) HC3 sandwich estimator. Provides more robustness in smaller samples than HC0 Long & Ervin (2000)
    'hac': Newey-West (1987) estimator for robustness to heteroscedasticity as well as serial auto-correlation at given lags.

    Args:
        vals (np.ndarray): 1d array of residuals
        X (np.ndarray): design matrix used in OLS
        robust_estimator (str): estimator type, 'hc0' (default), 'hc3', 'hac', or 'cluster'
        n_lags (int): number of lags, only used with 'hac' estimator, default is 1
        cluster (np.ndarry): array of cluster ids

    Returns:
        stderr (np.ndarray): 1d array of standard errors with length == X.shape[1]

    """

    assert robust_estimator in ['hc0','hc3','hac','cluster'], "robust_estimator must be one of hc0, hc3, hac, or cluster"

    # Make a sandwich!
    # First we need bread
    bread = np.linalg.pinv(np.dot(X.T,X))

    # Then we need meat
    if robust_estimator == 'hc0':
        V = np.diag(vals**2)
        meat = np.dot(np.dot(X.T,V),X)

    if robust_estimator == 'cluster':
        # Good ref: http://projects.iq.harvard.edu/files/gov2001/files/sesection_5.pdf
        if cluster is None:
            raise ValueError("data column identifying clusters must be provided")
        else:
            u = vals[:,np.newaxis] * X
            u = pd.DataFrame(u)
            # Use pandas groupby to get cluster-specific residuals
            u['Group'] = cluster
            u_clust = u.groupby('Group').sum()
            num_grps = u['Group'].nunique()
            meat = (num_grps / (num_grps -1)) * (X.shape[0] / (X.shape[0] - X.shape[1])) * u_clust.T.dot(u_clust)

    elif robust_estimator == 'hc3':
        V = np.diag(vals**2)/(1-np.diag(np.dot(X,np.dot(bread,X.T))))**2
        meat = np.dot(np.dot(X.T,V),X)

    elif robust_estimator == 'hac':
        weights = 1 - np.arange(n_lags+1.)/(n_lags+1.)

        #First compute lag 0
        V = np.diag(vals**2)
        meat = weights[0] * np.dot(np.dot(X.T,V),X)

        #Now loop over additional lags
        for l in range(1, n_lags+1):

            V = np.diag(vals[l:] * vals[:-l])
            meat_1 = np.dot(np.dot(X[l:].T,V),X[:-l])
            meat_2 = np.dot(np.dot(X[:-l].T,V),X[l:])

            meat += weights[l] * (meat_1 + meat_2)

    # Then we make a sandwich
    vcv = np.dot(np.dot(bread,meat),bread)

    return np.sqrt(np.diag(vcv))

def _ols(x,y,robust,n_lags,cluster,all_stats=True):
    """
    Compute OLS on data given formula. Useful for single computation and within permutation schemes.
    """

    # Expects as input pandas series and dataframe
    Y,X = y.values.squeeze(), x.values

    # The good stuff
    b = np.dot(np.linalg.pinv(X),Y)

    if all_stats:

        res = Y - np.dot(X,b)

        if robust:
            se = _robust_estimator(res,X,robust_estimator=robust,n_lags=n_lags,cluster=cluster)
        else:
            sigma = np.std(res,axis=0,ddof=X.shape[1])
            se = np.sqrt(np.diag(np.linalg.pinv(np.dot(X.T,X)))) * sigma

        t = b / se

        return b, se, t, res

    else:
        return b

def _chunk_perm_ols(x,y,robust,n_lags,cluster,seed):
    """
    Permuted OLS chunk.
    """
    # Shuffle y labels
    y = y.sample(frac=1,replace=False,random_state=seed)
    b, s, t, res = _ols(x,y,robust,n_lags,cluster,all_stats=True)

    return list(t)

def _chunk_boot_ols_coefs(dat,formula,seed):
    """
    OLS computation of coefficients to be used in a parallelization context.
    """
    # Random sample with replacement from all data
    dat = dat.sample(frac=1,replace=True,random_state=seed)
    y,x = dmatrices(formula,dat,1,return_type='dataframe')
    b = _ols(x,y,robust=None,n_lags=1,cluster=None,all_stats=False)
    return list(b)

def _perm_find(arr,x):
    """
    Find permutation cutoff in array.
    """
    return np.sum(np.abs(arr) >= np.abs(x))/float(len(arr))

def _isPD(mat):
    '''check if matrix is positive semi definite by virtue of whether numpy can perform cholesky decomposition on it.'''
    try:
        _ = np.linalg.cholesky(mat)
        return True
    except np.linalg.LinAlgError:
        return False

def _nearestPD(A, nit=100):
    """
    Higham (2000) algorithm to find the nearest positive semi-definite matrix that minimizes the Frobenius distance/norm.
    Pretty sure this is what statsmodels uses, in corr_nearest.
    Reference: https://goo.gl/Eut7UU

    Args:
        nit (int): number of iterations to run algorithm; more iterations improves accuracy but increases computation time.
    """

    n = A.shape[0]
    W = np.identity(n)

    def _getAplus(A):
        eigval, eigvec = np.linalg.eig(A)
        Q = np.matrix(eigvec)
        xdiag = np.matrix(np.diag(np.maximum(eigval, 0)))
        return Q*xdiag*Q.T

    def _getPs(A, W=None):
        W05 = np.matrix(W**.5)
        return  W05.I * _getAplus(W05 * A * W05) * W05.I

    def _getPu(A, W=None):
        Aret = np.array(A.copy())
        Aret[W > 0] = np.array(W)[W > 0]
        return np.matrix(Aret)

    # W is the matrix used for the norm (assumed to be Identity matrix here)
    # the algorithm should work for any diagonal W
    deltaS = 0
    Yk = A.copy()
    for k in range(nit):
        Rk = Yk - deltaS
        Xk = _getPs(Rk, W=W)
        deltaS = Xk - Rk
        Yk = _getPu(Xk, W=W)
    return Yk

def upper(mat):
    '''Return upper triangle of matrix'''
    idx = np.triu_indices_from(mat,k=1)
    return mat[idx]

def _return_t(model):
    '''Return t or z stat from R model summary.'''
    summary = base.summary(model)
    unsum = base.unclass(summary)
    return pandas2ri.ri2py(unsum.rx2('coefficients'))[:,-1]
