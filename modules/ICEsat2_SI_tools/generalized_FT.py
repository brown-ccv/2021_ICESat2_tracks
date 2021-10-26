
import numpy as np



import spectral_estimates as spec


class wavenumber_spectrogram_gFT(object):
    def __init__(self, x, data, L, dx, wavenumber, dy = None, ov=None):
        """
        returns a wavenumber spectrogram with the resolution L-ov
        this uses Lombscargle

        inputs:
        data    data
        x       x-positions of where the data is taken
        dy      passed to LS object "error or sequence of observational errors associated with times t"
        waven_method     ('auto' (default), or something else if "waven_method" is an array these wavenumbers are used )

        L       window length in units of x
        ov      (default=None) number of grid points the windows should overlab
        window  (default=np.hanning) numpy window function

        returns:
        xr.Dataset with x, k as cooridates of the spectrogram and the mean error
            other arributes are in the .attr dict.
        """

        self.Lmeters      = L
        self.ov = int(L/2) if ov is None else ov #when not defined in create_chunk_boundaries then L/2

        self.x      = x
        self.dx     = dx
        self.data   = data
        self.error = dy if dy is not None else None
        self.Lpoints= int(self.Lmeters/self.dx)

        # create subsample k
        self.k, self.dk  = wavenumber, np.diff(wavenumber).mean()

    def cal_spectrogram(self, x = None, data=None, error=None, name=None, xlims =None, max_nfev = None, map_func=None, plot_flag = False):

        """
        defines apply function and calculated all sub-sample sprectra using map

        input:
        x (None)
        data (None)
        error (None)
        name (None)

        creates:
        all kinds of self. variables:
        self.G is an xr.DataArray with the best guess spectral power.
        self.GG is a xr.Dataset with the best guess conmplex conjugate and the rar spectral power

        returns:
        self.GG, params_dataframe
            params_dataframe is a pd.DataFrame that containes all the parameters of the fitting process (and may contain uncertainties too once they are calculated)
        """
        import xarray as xr
        import copy
        import pandas as pd

        X       = self.x if x is None else x # all x positions
        DATA    = self.data if data is None else data # all data points
        ERR     = self.error if error is None else error # all error for points
        Lmeters, dk   = self.Lmeters, self.dk
        Lpoints = self.Lpoints
        #win     = self.win
        self.xlims = ( np.round(X.min()), X.max() ) if xlims is None else xlims

        # init Lomb scargle object with noise as nummy data ()
        #dy_fake= np.random.randn(len(dy))*0.001 if self.dy is not None else None
        #self.LS = LombScargle(X[0:L] , np.random.randn(L)*0.001, fit_mean=True)

        # define  weight function

        def get_weights_from_data(x, y, dx, stancil, k, plot_flag=False, method = 'gaussian' ):


            """
            x,y,    x postions and y data, on any (regular) postion, has gaps
            dx      dx
            stancil stancil from stancil_iter.defines boundaries of segment
            k       wavenumbers of inversion problem

            returns:
            peak-normalized weights in the size of k
            """
            #make y gridded
            x_pos            = (np.round( (x - stancil[0])/ dx -1 , 0) ).astype('int')
            x_model          = np.arange(stancil[0], stancil[-1], dx)
            y_gridded        = np.copy(x_model) * 0
            y_gridded[x_pos] = y
            #nan_mask         =np.isnan(y_gridded)


            # def gaus(x, x_0, amp, sigma_g ):
            #     return amp* np.exp(-0.5 * (  (x-x_0)/sigma_g)**2)
            # weight = data_weight.mean() * gaus(self.k, 0.05, 1 , 0.02)**(1/2)
            # weight = weight *10+ weight.max()* 0.005 # add pemnalty floor

            # take FFT to get peaj parameters
            k_fft = np.fft.rfftfreq(x_model.size, d=dx) * 2* np.pi
            f_weight= np.sqrt(9.81 * k_fft) / (2 *np.pi)
            data_weight = spec.Z_to_power(np.fft.rfft(y_gridded), np.diff(f_weight).mean(), x_pos.size)

            Spec_fft = get_prior_spec(f_weight, data_weight )

            pars = Spec_fft.set_parameters(flim= np.sqrt(9.81 *self.k[-1] ) /2/np.pi)
            k_max = (pars['f_max'].value *2 *np.pi)**2/ 9.81
            #print('k_max ', k_max)

            if method is 'gaussian':
                # simple gaussian weight
                def gaus(x, x_0, amp, sigma_g ):
                    return amp* np.exp(-0.5 * (  (x-x_0)/sigma_g)**2)

                weight = gaus(self.k, k_max, 1 , 0.02)**(1/2)
                #weight = weight *1+ weight.max()* 0.1 # add pemnalty floor
                params = None

            elif method is 'parametric':

                # JONSWAP weight
                f= np.sqrt(9.81 * k) / (2 *np.pi)
                #weight = weight + weight.max()* 0.1 # add pemnalty floor
                # optimzes paramteric function to data
                weight = Spec_fft.create_weight(freq = f, plot_flag= False, max_nfev=max_nfev)

                if plot_flag:
                    Spec_fft.fitter.params.pretty_print()

                params = Spec_fft.fitter.params
                #weight = weight+ weight.max()* 0.05 # add pemnalty floor
            else:
                raise ValueError(" 'method'  must be either 'gaussian' or 'parametric' ")

            # add pemnalty floor
            weight = weight + weight.max()* 0.1


            if plot_flag:
                import matplotlib.pyplot as plt

                #plt.plot(k_fft[1:], Spec_fft.model_func(Spec_fft.freq, pars), 'b--' )

                plt.plot(k, weight, zorder=12)
                plt.plot(k_fft, data_weight)
                plt.xlim(k[0],k[-1] )
                plt.show()


            # peak normlize weight
            weight = weight/weight.max()

            return weight, params


        def calc_gFT_apply(stancil):

            """
            windows the data accoding to stencil and applies LS spectrogram
            returns: stancil center, spectrum for this stencil, number of datapoints in stancil
            """
            from scipy.signal import detrend
            import matplotlib.pyplot as plt

            #x = X[stancil[0]:stancil[-1]]
            x_mask= (stancil[0] <= X) & (X <= stancil[-1])

            #print(stancil[1])
            x = X[x_mask]
            if x.size/Lpoints < 0.1: # if there are not enough photos set results to nan
                #return stancil[1], self.k*np.nan, np.fft.rfftfreq( int(self.Lpoints), d=self.dx)*np.nan,  x.size
                return stancil[1], np.concatenate([self.k*np.nan , self.k*np.nan]), np.nan,  np.nan, x.size
            y = DATA[x_mask]
            y_var = y.var()

            FT = generalized_Fourier(x, y, self.k)
            H = FT.get_H()


            # define weights
            weight, prior_pars = get_weights_from_data(x, y, self.dx, stancil, self.k, plot_flag=plot_flag, method='parametric')
            weight = weight * y_var

            # define error
            noise_amp = 10
            err = ERR[x_mask] if ERR is not None else 1
            err = (err/ err.std()) * y_var * noise_amp

            FT.define_problem(1/weight, err) # 1st arg is Penalty, 2nd is error

            b_hat = FT.solve()

            x_pos               = (np.round( (x - stancil[0])/ self.dx -1 , 0) ).astype('int')
            eta                 =  np.arange(0, self.Lmeters + self.dx, self.dx) - self.Lmeters/2
            y_model_grid        = np.copy(eta) *np.nan
            y_model_grid[x_pos] = FT.model()

            inverse_stats = FT.get_stats(print_flag=True)
            # add fitting parameters of Prior to stats dict
            for k,I in prior_pars.items():
                inverse_stats[k] = I.value

            # Z = complex_represenation(b_hat, FT.M, Lpoints )

            PSD = power_from_model(b_hat, dk,  self.k.size,  x.size,  Lpoints) #Z_to_power_gFT(b_hat, dk, x.size,  Lpoints )

            if self.k.size*2 > x.size:
                col = 'red'
            else:
                col= 'blue'

            if plot_flag:
                plt.plot(self.k, PSD, color=col )
                plt.title( 'b_hat power, 2M='+ str(self.k.size*2) + ', N='+ str(x.size))
                plt.xlim(self.k[0],self.k[-1])

                plt.show()

                print('---------------------------------')

            return stancil[1], b_hat, inverse_stats, y_model_grid ,  x.size

        # % derive L2 stancil
        self.stancil_iter = spec.create_chunk_boundaries_unit_lengths(Lmeters, self.xlims, ov= self.ov, iter_flag=True)
        #stancil_iter = create_chunk_boundaries_unit_lengths(L, ( np.round(X.min()), X.max() ), ov= self.ov, iter_flag=True)

        # apply func to all stancils
        # Spec_returns=list()
        # for ss in stancil_iter:
        #     print(ss)
        #     Spec_returns.append( calc_spectrum_apply(ss) )

        map_func = map if map_func is None else map_func
        print(map_func)
        Spec_returns = list(map_func( calc_gFT_apply, copy.copy(self.stancil_iter)   ))
        # # linear version
        #Spec_returns = list(map( calc_spectrum_and_field_apply, copy.copy(self.stancil_iter)   ))

        # unpack resutls of the mapping:
        GFT_model       = dict()
        Z_model         = dict()

        D_specs         = dict()
        D_specs_model   = dict()

        Pars            = dict()
        y_model         = dict()
        N_per_stancil   = list()

        for I in Spec_returns:
            #print(I[1].shape, I[2].shape)

            GFT_model[I[0]]     = (I[1][0:self.k.size],  I[1][self.k.size:])
            Z_model[I[0]]       = Z = complex_represenation(I[1], self.k.size, Lpoints )

            PSD_data, PSD_model =  Z_to_power_gFT(Z, self.dk, I[4], Lpoints )
            D_specs[I[0]]       = PSD_data
            D_specs_model[I[0]] = PSD_model

            Pars[I[0]]         = I[2]
            y_model[I[0]]       = I[3]
            N_per_stancil.append(I[4])


        self.N_per_stancil = N_per_stancil
        chunk_positions = np.array(list(D_specs.keys()))
        self.N_stancils    = len(chunk_positions) # number of spectral realizatiobs

        # repack data, create xarray
        # 1st LS spectal estimates
        G_LS_power =dict()
        for xi,I in D_specs.items():
            G_LS_power[xi] = xr.DataArray(I,  dims=['k'], coords={'k': self.k, 'x': xi } , name='gFT_PSD_data')

        G_LS_power = xr.concat(G_LS_power.values(), dim='x').T#.to_dataset()

        G_LS_power_model =dict()
        for xi,I in D_specs_model.items():
            G_LS_power_model[xi] = xr.DataArray(I,  dims=['k'], coords={'k': self.k, 'x': xi } , name='gFT_PSD_model')

        G_LS_power_model = xr.concat(G_LS_power_model.values(), dim='x').T#.to_dataset()
        self.G = G_LS_power_model
        self.G.name = 'gFT_PSD_model'

        #2nd FFT(Y_model)
        G_model =dict()
        for xi,I in Z_model.items():
            # if I.size < Y_model_k_fft.size:
            #     I = np.insert(I, -1, I[-1])
            G_model[xi] = xr.DataArray(I,  dims=['k'], coords={'k': self.k, 'x': xi } , name='Z_hat')
        G_model = xr.concat(G_model.values(), dim='x').T#.to_dataset()

        # 3rd
        GFT_model_coeff_A =dict()
        GFT_model_coeff_B =dict()

        for xi,I in GFT_model.items():
            # if I.size < Y_model_k_fft.size:
            #     I = np.insert(I, -1, I[-1])
            GFT_model_coeff_A[xi] = xr.DataArray(I[0],  dims=['k'], coords={'k': self.k, 'x': xi } , name='gFT_cos_coeff')
            GFT_model_coeff_B[xi] = xr.DataArray(I[1],  dims=['k'], coords={'k': self.k, 'x': xi } , name='gFT_sin_coeff')

        GFT_model_coeff_A = xr.concat(GFT_model_coeff_A.values(), dim='x').T#.to_dataset()
        GFT_model_coeff_B = xr.concat(GFT_model_coeff_B.values(), dim='x').T#.to_dataset()

        # 4rd: model in real space
        y_model_eta =dict()
        eta   =  np.arange(0, self.Lmeters + self.dx, self.dx) - self.Lmeters/2
        for xi,I in y_model.items():
            y_model_eta[xi] = xr.DataArray(I,  dims=['eta'], coords={'eta': eta, 'x': xi } , name="y_model")
        y_model_eta = xr.concat(y_model_eta.values(), dim='x').T#.to_dataset()

        # merge wavenumber datasets
        self.GG = xr.merge([G_LS_power, G_LS_power_model, G_model, GFT_model_coeff_A, GFT_model_coeff_B])
        self.GG.attrs['ov'] = self.ov
        self.GG.attrs['L'] = self.Lmeters
        self.GG.attrs['Lpoints'] = self.Lpoints
        self.GG.coords['N_per_stancil'] = ( ('x'), N_per_stancil)

        #self.GG.expand_dims(dim='eta')
        # eta   =  np.arange(0, self.L + self.dx, self.dx) - self.L/2
        # self.GG.coords['eta'] = ( ('eta'), eta )
        # #self.GG['win'] = ( ('eta'), np.insert(self.win, -1, self.win[-1]))


        # create dataframe with fitted parameters and derive y_model and errors
        # reduce to valid values
        PP2= dict()
        for k, I in Pars.items():
            if I is not np.nan:
                PP2[k] =I

        keys = Pars[next(iter(PP2))].keys()
        keys_DF = set(keys) - set(['model_error_k', 'model_error_x'])
        params_dataframe = pd.DataFrame(index =keys_DF)
        model_error_k_cos =dict()
        model_error_k_sin =dict()
        model_error_x =dict()
        for xi,I in Pars.items():

            if I is not np.nan:

                params_dataframe[xi] = [I[ki] for ki in keys_DF]

                model_error_k_cos[xi]    = xr.DataArray(I['model_error_k'][0:self.k.size],  dims=['k'], coords={'k': self.k, 'x': xi } , name='model_error_k_cos')
                model_error_k_sin[xi]    = xr.DataArray(I['model_error_k'][self.k.size:],  dims=['k'], coords={'k': self.k, 'x': xi } , name='model_error_k_sin')

                sta, ste    = xi- self.Lmeters/2, xi+self.Lmeters/2
                #x_mask= (sta <= X) & (X <= ste)
                x_pos =  (np.round( (X[(sta <= X) & (X <= ste)] - sta)/ self.dx , 0) ).astype('int')
                x_err = np.copy(eta) *np.nan
                x_err[x_pos] = I['model_error_x']
                model_error_x[xi]    = xr.DataArray(x_err,  dims=['eta'], coords={'eta': eta, 'x': xi } , name='model_error_x')

            else:
                model_error_k_cos[xi]    = xr.DataArray(np.zeros(self.k.size)*np.nan,  dims=['k'], coords={'k': self.k, 'x': xi } , name='model_error_k_cos')
                model_error_k_sin[xi]    = xr.DataArray(np.zeros(self.k.size)*np.nan,  dims=['k'], coords={'k': self.k, 'x': xi } , name='model_error_k_sin')

                model_error_x[xi]    = xr.DataArray(np.copy(eta) *np.nan,  dims=['eta'], coords={'eta': eta, 'x': xi } , name='model_error_x')


        self.GG['model_error_k_cos'] = xr.concat(model_error_k_cos.values(), dim='x').T
        self.GG['model_error_k_sin'] = xr.concat(model_error_k_sin.values(), dim='x').T

        model_error_x = xr.concat(model_error_x.values(), dim='x').T
        GG_x= xr.merge([y_model_eta, model_error_x])
        #model_error_x

        return self.GG, GG_x, params_dataframe



    def calc_var(self):

        Gmean = np.nanmean(self.G, 1)
        infmask = np.isinf(Gmean)

        return self.dk * Gmean[~infmask].sum().data

    # def parceval(self, add_attrs=True ):
    #     return wavenumber_spectrogram.parceval(self, add_attrs= add_attrs )

    def parceval(self, add_attrs=True, weight_data=False ):
        "test Parceval theorem"
        import copy
        DATA = self.data
        L = self.Lmeters
        X = self.x

        # derive mean variances of stancils
        #stancil_iter = create_chunk_boundaries_unit_lengths(L, self.xlims, ov= self.ov )

        def get_stancil_var_apply(stancil):
            from scipy.signal import detrend
            "returns the variance of yy for stancil"
            x_mask= (stancil[0] < X) & (X <= stancil[-1])
            idata = DATA[x_mask]
            if len(idata) < 1:
                return stancil[1], np.nan, len(idata)
            idata = detrend(idata)
            # weight data
            x_pos =  (np.round( (X[x_mask] - stancil[0])/ 10.0 , 0) ).astype('int')
            if weight_data:
                window = self.win[x_pos]
                idata = idata * window * np.sqrt( np.var(idata)  / np.var(( idata* window) ) )
            return stancil[1], idata.var(), len(idata)

        D_vars = list(map(get_stancil_var_apply, copy.copy(self.stancil_iter)  ))

        stancil_vars, Nphotons =list(), 0
        for I in D_vars:
            stancil_vars.append(I[1]  * I[2])
            Nphotons  += I[2]

        stancil_weighted_variance = np.nansum(np.array(stancil_vars))/Nphotons

        print('Parcevals Theorem:')
        print('variance of timeseries: ', DATA.var())
        print('mean variance of stancils: ', stancil_weighted_variance )
        #print('variance of weighted timeseries: ',self.phi.var() )
        #self.calc_var(self)
        print('variance of the optimzed windowed LS Spectrum: ', self.calc_var())

        if add_attrs:
            self.G.attrs['variance_unweighted_data']        = DATA.var()
            self.G.attrs['mean_variance_stancils']  = np.nanmean(np.array(stancil_vars) )
            self.G.attrs['mean_variance_LS_pwelch_spectrum']   = self.calc_var()


    def mean_spectral_error(self, mask=None, confidence = 0.95 ):
        return wavenumber_spectrogram.mean_spectral_error(self, mask=mask, confidence= confidence )




def complex_represenation(b_hat, M, N_x_full):
    """
    returns the fourrier coefficiens in b_hat as comples number Z
    b_hat is the model coefficient matrix
    M   number if wavenumbers, ie.e size of the model matrix /2
    N_x_full it the size of the data if there wouldn't be gaps. = Lmeters / dx
    i.e twice the size of the wavenumber space of a standard FFT.

    returns:
    The fourier coeffcients as complex vector with the same amplitudes as from an fft, but for the model wavenumbers of b_hat.

    this returns a power spectral density with the same variance as the data without gaps.
    """
    Z = b_hat[0:M] - b_hat[M:] *1j
    Z = Z * (N_x_full/2+1) # this
    return Z


def Z_to_power_gFT(Z, dk, N_x,  N_x_full):
    """ compute the 1d Power spectral density of a field Z
    inputs:
    Z       complex fourier coefficients, output of .complex_represenation method
    dk      delta wavenumber asssuming Z is on regular grid
    N_x the actual size of the data
    N_x_full it the size of the data if there wouldn't be gaps. = Lmeters / dx

    returns
    spec_incomplete     spectral density respesenting the incomplete data ( [b_hat]^2 / dk)
    spec_complete       spectal density representing the (moddeled) complete data ( [b_hat]^2 / dk)
    """

    spec = 2.*(Z*Z.conj()).real
    neven = True if (N_x_full%2) else False
    # the zeroth frequency should be counted only once
    spec[0] = spec[0]/2.
    if neven:
        spec[-1] = spec[-1]/2.

    # spectral density respesenting the incomplete data ( [b_hat]^2 / dk)
    spec_incomplete = spec / dk / N_x / N_x_full
    # spectal density representing the (moddeled) complete data ( [b_hat]^2 / dk)
    spec_complete = spec / dk / N_x_full**2


    return spec_incomplete, spec_complete

def power_from_model(b_hat, dk,  M,  N_x,  N_x_full):
    """ compute the 1d Power spectral density from the model coefficients in b_hat

    b_hat       is the model coefficient matrix
    M     size of the model vector/2, size of k
    N_x the     actual size of the data
    N_x_full    is the size of the data if there wouldn't be gaps. = Lmeters / dx

    returns:

    spectral density respesenting the incomplete data ( [b_hat]^2 / dk)
    """

    Z = b_hat[0:M] - b_hat[M:] *1j
    spec = (Z*Z.conj()).real * N_x_full / 2 / N_x /dk

    neven = True if (N_x_full%2) else False
    spec[0] = spec[0]/2.
    if neven:
        spec[-1] = spec[-1]/2.

    # spectral density respesenting the incomplete data
    return spec

class generalized_Fourier(object):
    def __init__(self, x, ydata, k ):

        import numpy as np
        from numpy import linalg


        self.x, self.ydata, self.k = x, ydata, k
        self.M = self.k.size # number of wavenumbers
        self.N = self.x.size# number of datapoints

    # data matrix
    def get_H(self, xx = None):
        xx = self.x if xx is None else xx
        self.H = np.vstack([ np.cos(np.outer(xx, self.k)).T ,  np.sin(np.outer(xx, self.k)).T ] ).T
        return self.H

    def define_problem(self, penalty, data_uncertainty):
        """
        weight     1/Prior or 1/penealty in units of the data (?), vector or length M
        data_uncertainty (observed) uncertainy of the data, can be vector of length N, or scaler
        """

        self.H = self.get_H()
        penalties = np.concatenate([ penalty ,  penalty  ])
        self.P  = np.diag(1/penalties) # penalty  2M x 2M
        self.R  = np.diag(  data_uncertainty) #Noise Prior N x N

    def solve(self):
        from numpy import linalg
        inv = linalg.inv
        " solves the linear inverse problem, return hessian and b_hat"

        H = self.H
        P = self.P
        R = self.R
        y = self.ydata

        Hess        = (H.T @ inv(R) @ H  ) + inv(P)
        Hess_inv    = inv( Hess)
        b_hat       = Hess_inv @ H.T @ inv(R) @ y

        self.Hess, self.Hess_inv, self.b_hat = Hess, Hess_inv, b_hat

        return b_hat

    def model(self):
        if 'b_hat' not in self.__dict__:
            raise ValueError('b_hat does not exist, please invert for model first')
        return (self.b_hat * self.H).sum(1)


    def get_stats(self, print_flag=False):

        model_error_k         = np.diag(self.Hess_inv)
        model_error_real  = ((self.H**2) @ self.Hess_inv).sum(1)

        residual = self.ydata - self.model()

        pars = {
        'data_var': self.ydata.var(),
        'model_var': self.model().var(),
        'residual_var' : residual.var(),
        'normalized_residual' : residual.var() /np.diag(self.R).mean(),
        'model_error_k' : np.diag(self.Hess_inv),
        'model_error_x' : ((self.H**2) @ self.Hess_inv).sum(1),
        'var_sum' : self.ydata.var() - self.model().var() -residual.var()
        }

        if print_flag:
            for ki in ['data_var', 'model_var', 'residual_var', 'normalized_residual','var_sum']:

                print( ki.ljust(20) + str(pars[ki]) )


        return pars



class get_prior_spec(object):
    def __init__(self, freq, data):


        """

        """
        import numpy as np
        import lmfit as LM
        self.LM =LM
        self.data = data
        self.freq = freq

        if self.freq[0] ==0:

            self.freq_cut_flag= True
            self.freq, self.data = self.freq[1:], self.data[1:]
        else:
            self.freq_cut_flag= False

    def set_parameters(self, flim=None):
        import numpy as np
        params = self.LM.Parameters()

        def get_peak_pos(y, smooth =30):

            yy =self.runningmean(y, smooth, tailcopy=False)
            yy[np.isnan(yy)] = 0
            return yy.argmax()

        if flim is not None:
            iflim= abs(self.freq - flim).argmin()
            f_max = self.freq[0:iflim][ get_peak_pos(abs(self.data[0:iflim]), 30) ]
        else:
            f_max = self.freq[ get_peak_pos(abs(self.data), 30) ]

        self.f_max = f_max
        # p_smothed = self.runningmean(np.abs(self.Z ), 20, tailcopy=True)
        # f_max = self.freq[p_smothed[~np.isnan(p_smothed)].argmax()]
        params.add('f_max', f_max , min=f_max*0.2, max=f_max*1.5,  vary=True)
        params.add('amp', 1       , min=.0001, max=5,  vary=True)
        params.add('gamma', 1     , min=1, max=3.3,  vary=False)
        params.add('alpha', 1     , min=0, max= 0.95 * np.pi /2,  vary=True)

        self.params = params
        return params

    def model_func(self, f, params):
        return self.non_dim_spec_model(f, params['f_max'].value, params['amp'].value, params['gamma'].value)

    def non_dim_spec_model(self, f, f_max, amp, gamma=1, angle_rad = 0):
        import JONSWAP_gamma as spectal_models
        U= 20 # results are incensitive to U
        f_true = f * np.cos(angle_rad)
        model= spectal_models.JONSWAP_default_alt(f_true, f_max, 20 ,  gamma=gamma)
        model = amp * model/np.nanmean(model)
        model[np.isnan(model)] =0
        return model

    def objective_func(self,params, data, model_func, freq, weight= None):
        f_weight = np.linspace(1, .1,  freq.size)
        model = model_func(freq, params)
        cost =( abs(data - model) * f_weight / data.std() )**2
        return cost.sum() + 4 *np.abs(self.f_max -params['f_max'])**2 / self.f_max



    def test_ojective_func(self, model_func):
        return self.objective_func(self.params, self.data, model_func, self.freq)


    def optimize(self, fitting_args= None , method='dual_annealing', max_nfev=None):


        if fitting_args is None:
            fitting_args = (self.data, self.model_func, self.freq)

        self.weight_func = fitting_args[1]
        self.fitter = self.LM.minimize(self.objective_func, self.params, args=fitting_args, method=method, max_nfev=max_nfev)
        return self.fitter

    def plot_data(self):
        import matplotlib.pyplot as plt
        plt.plot(self.freq, self.data, 'k')

    def plot_model(self, pars):
        import matplotlib.pyplot as plt
        plt.plot(self.freq, self.model_func(self.freq, pars), 'b--' )

    def runningmean(self, var, m, tailcopy=False):
        m=int(m)
        s =var.shape
        if s[0] <= 2*m:
            print('0 Dimension is smaller then averaging length')
            return
        rr=np.asarray(var)*np.nan
        #print(type(rr))
        var_range=np.arange(m,int(s[0])-m-1,1)
        for i in var_range[np.isfinite(var[m:int(s[0])-m-1])]:
            #rm.append(var[i-m:i+m].mean())
            rr[int(i)]=np.nanmean(var[i-m:i+m])
        if tailcopy:
            rr[0:m]=rr[m+1]
            rr[-m-1:-1]=rr[-m-2]

        return rr


    def create_weight(self,freq=None, plot_flag=True, flim= None, max_nfev=None):
        """
        this returns a weight function that can be used for the Least square fitting.
        """

        if freq is None:
            ff = self.freq
        else:
            ff = freq
        if 'params' not in self.__dict__:
            self.set_parameters(flim=flim)

        self.optimize(max_nfev=max_nfev)

        if plot_flag:
            self.fitter.params.pretty_print()
            self.plot_data()
            self.plot_model(self.fitter.params)


        result = self.model_func(ff, self.fitter.params)
        if self.freq_cut_flag and freq is None:
            result = np.insert(result, 0, 0)

        return result
