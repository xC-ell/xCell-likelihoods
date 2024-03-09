import os
import pickle
import sacc
import numpy as np
import pyccl as ccl
from .profiles import (HaloProfileDensityHE,
                       HaloProfilePressureHE,
                       HaloProfileXray,
                       HaloProfileNFWBaryon,
                       XrayTracer)


class ROSATxLike(object):
    def __init__(self, year=3,
                 params_vary=['lMc', 'alpha_T', 'eta_b',
                              'gamma', 'logTAGN'],
                 priors={'lMc': [13.0, 15.0],
                         'alpha_T': [0.5, 2.0],
                         'eta_b': [0.2, 2.0],
                         'gamma': [1.1, 1.5],
                         'logTAGN': [7.5, 8.2]},
                 bins=[0, 1, 2, 3], Zmetal=0.3, lines=True, lmin=30,
                 lmax=2048, mbias=[-0.0063, -0.0198, -0.0241, -0.0369],
                 zbias=[0.0, 0.0, 0.0, 0.0], with_clumping=True,
                 spec_pyatomdb=True, use_baryonic_matter=False,
                 A_IA=None):
        self.params_vary = params_vary
        self.priors = priors
        self.Zmetal = Zmetal
        self.lines = lines
        self.wclump = with_clumping
        self.spec_pyatomdb = spec_pyatomdb
        self.use_baryonic_matter = use_baryonic_matter
        self.A_IA = A_IA

        # Load data
        s = sacc.Sacc.load_fits(f'data/cls_cov_Y{year}iv_marg_dz_m.fits')
        # Scale cuts
        s.keep_selection(ell__gt=lmin, ell__lt=lmax)
        self.bins = bins
        # Read info about all C_ells and the dNdzs
        indices = []
        self.cl_meta = []
        self.dndzs = {}
        for i in self.bins:
            tn = f'DESY{year}wl__{i}'
            leff, cld, cov, ind = s.get_ell_cl('cl_0e', 'ROSAT', tn,
                                               return_cov=True,
                                               return_ind=True)
            indices += list(ind)
            self.cl_meta.append({'t1': 'ROSAT',
                                 't2': tn,
                                 'ls': leff,
                                 'cld': cld,
                                 'cov': cov,
                                 'icov': np.linalg.inv(cov),
                                 'ind': ind,
                                 'mplusone': 1+mbias[i],
                                 'beam': self.beam(leff)})
            tr = s.tracers[tn]
            self.dndzs[tn] = (tr.z+zbias[i], tr.nz)
        indices = np.array(indices)

        # Reorganise data
        self.data = s.mean[indices]
        self.cov = s.covariance.dense[indices][:, indices]
        self.inv_cov = np.linalg.inv(self.cov)
        self.ndata = len(self.data)

        # Get X-ray spectrum
        self.get_spectrum()

        # Initialise theory model
        self.init_model()

    def beam(self, ell, nside=1024):
        fwhm_hpx_amin = 60*41.7/nside
        sigma_hpx = np.radians(fwhm_hpx_amin/60)/2.355
        sigma_ROSAT = np.radians(1.8e0/60)/2.355
        sigma_tot_2 = sigma_ROSAT**2 + 2*sigma_hpx**2
        return np.exp(-0.5*sigma_tot_2*ell*(ell+1))

    def get_spectrum(self):
        emin = 0.5
        emax = 2.0

        fname = 'data/J'
        if self.lines:
            fname += 'tot'
        else:
            fname += 'cont'
        fname += '_Z%.2lf' % self.Zmetal
        fname += '_Emin%.2lf' % emin
        fname += '_Emax%.2lf' % emax
        if self.spec_pyatomdb:
            fname += '_padb'
        fname += '.pck'
        if os.path.isfile(fname):
            with open(fname, "rb") as f:
                J = pickle.load(f)
        else:
            raise ValueError("J factor should be precomputed")
        self.J = J

    def init_model(self):
        COSMO_P18 = {"Omega_c": 0.26066676,
                     "Omega_b": 0.048974682,
                     "h": 0.6766,
                     "n_s": 0.9665,
                     "sigma8": 0.8102,
                     "matter_power_spectrum": "linear"}
        self.cosmo = ccl.Cosmology(**COSMO_P18)
        mdef = ccl.halos.MassDef200c
        cM = ccl.halos.ConcentrationDuffy08(mass_def=mdef)
        nM = ccl.halos.MassFuncTinker08(mass_def=mdef)
        bM = ccl.halos.HaloBiasTinker10(mass_def=mdef)
        self.hmc = ccl.halos.HMCalculator(
            mass_function=nM, halo_bias=bM, mass_def=mdef,
            log10M_max=15., log10M_min=10, nM=32)
        self.prof_dens = HaloProfileDensityHE(mass_def=mdef,
                                              concentration=cM,
                                              kind='n_total')
        self.prof_pres = HaloProfilePressureHE(mass_def=mdef,
                                               concentration=cM,
                                               kind='n_total')
        if self.use_baryonic_matter:
            self.prof_matter = HaloProfileNFWBaryon(
                mass_def=mdef, concentration=cM)
        else:
            self.prof_matter = ccl.halos.HaloProfileNFW(
                mass_def=mdef, concentration=cM)
        self.prof_xray = HaloProfileXray(
            mass_def=mdef, Jinterp=self.J,
            dens=self.prof_dens, pres=self.prof_pres,
            fourier_approx=False,
            with_clumping=self.wclump)
        # Initialize tracers
        self.tx = XrayTracer(self.cosmo)
        if self.A_IA is not None:
            ia = (np.linspace(0, 3, 1024),
                  self.A_IA*np.ones(1024))
        else:
            ia = None
        self.tgs = {k: ccl.WeakLensingTracer(self.cosmo, dndz=v,
                                             ia_bias=ia)
                    for k, v in self.dndzs.items()}

        # Fixed k and a arrays
        k_arr = np.geomspace(1e-4, 100, 256)
        self.lk_arr = np.log(k_arr)
        self.a_arr = np.linspace(0.3, 1, 8)

    def default_params(self):
        return {'lMc': 14.2, 'alpha_T': 1.0, 'eta_b': 0.5,
                'gamma': 1.19, 'logTAGN': None}

    def params_to_dict(self, pvec):
        pdict = self.default_params()
        for k, v in zip(self.params_vary, pvec):
            pdict[k] = v
        return pdict

    def update_params(self, pdict):
        kwargs = {k: pdict.get(k, None)
                  for k in ['lMc', 'alpha_T', 'eta_b',
                            'gamma', 'logTAGN']}
        self.prof_dens.update_parameters(**kwargs)
        self.prof_pres.update_parameters(**kwargs)
        if self.use_baryonic_matter:
            kwargs = {k: pdict.get(k, None)
                      for k in ['lMc', 'eta_b',
                                'gamma', 'logTAGN']}
            self.prof_matter.update_parameters(**kwargs)

    def get_model(self, **kwargs):
        self.update_params(kwargs)
        pkx = ccl.halos.halomod_Pk2D(self.cosmo, self.hmc,
                                     self.prof_matter, prof2=self.prof_xray,
                                     lk_arr=self.lk_arr, a_arr=self.a_arr)
        cls = []
        for clm in self.cl_meta:
            clt = ccl.angular_cl(self.cosmo,
                                 self.tx, self.tgs[clm['t2']],
                                 clm['ls'], p_of_k_a=pkx)
            bm = clm['beam']
            opm = clm['mplusone']
            cls.append(clt*bm*opm)
        return cls

    def get_prior(self, pdict):
        for k in self.params_vary:
            p0, pf = self.priors[k]
            p = pdict[k]
            if (p > pf) or (p < p0):
                return True
        return False

    def get_logp(self, per_bin=False, **kwargs):
        if self.get_prior(kwargs):
            return -np.inf
        model = self.get_model(**kwargs)
        t = np.concatenate(model)
        r = self.data - t
        chi2 = np.dot(r, np.dot(self.inv_cov, r))
        if per_bin:
            chi2s = [
                np.dot(clm['cld']-clt,
                       np.dot(clm['icov'], clm['cld']-clt))
                for clm, clt in zip(self.cl_meta, model)]
            chi2s.append(chi2)
            return -0.5*np.array(chi2s)
        return -0.5*chi2

    def logp(self, pvec, per_bin=False):
        try:
            pdict = self.params_to_dict(pvec)
            return self.get_logp(per_bin=per_bin, **pdict)
        except:
            return -np.inf
