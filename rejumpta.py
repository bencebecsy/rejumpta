################################################################################
#
#(PT)^2AMCMC -- Parallel Tempering Pulsar Timing Array MCMC
#
#Bence Bécsy (bencebecsy@montana.edu) -- 2019
################################################################################

import numpy as np
import healpy as hp
import matplotlib.pyplot as plt

import enterprise
import enterprise.signals.parameter as parameter
from enterprise.signals import signal_base

import enterprise_cw_funcs_from_git as models

################################################################################
#
#MAIN MCMC ENGINE
#
################################################################################

def run_ptmcmc(N, T_max, n_chain, base_model, pulsars, max_n_source=1, RJ_weight=0,
               regular_weight=3, PT_swap_weight=1,
               Fe_proposal_weight=0, fe_file=None, draw_from_prior_weight=0,
               de_weight=0):
    #setting up the pta object
    cws = []
    for i in range(max_n_source):
        log10_fgw = parameter.Uniform(np.log10(3.5e-9), -7)(str(i)+'_'+'log10_fgw')
        log10_mc = parameter.Constant(np.log10(5e9))(str(i)+'_'+'log10_mc')
        cos_gwtheta = parameter.Uniform(-1, 1)(str(i)+'_'+'cos_gwtheta')
        gwphi = parameter.Uniform(0, 2*np.pi)(str(i)+'_'+'gwphi')
        phase0 = parameter.Uniform(0, 2*np.pi)(str(i)+'_'+'phase0')
        psi = parameter.Uniform(0, np.pi)(str(i)+'_'+'psi')
        cos_inc = parameter.Uniform(-1, 1)(str(i)+'_'+'cos_inc')
        #log10_h = parameter.LinearExp(-18, -11)('log10_h'+str(i))
        log10_h = parameter.Uniform(-18, -11)(str(i)+'_'+'log10_h')
        cw_wf = models.cw_delay(cos_gwtheta=cos_gwtheta, gwphi=gwphi, log10_mc=log10_mc,
                     log10_h=log10_h, log10_fgw=log10_fgw, phase0=phase0,
                     psi=psi, cos_inc=cos_inc, tref=53000*86400)
        cws.append(models.CWSignal(cw_wf, psrTerm=False, name='cw'+str(i)))
    
    ptas = []
    for n_source in range(1,max_n_source+1):
        s = base_model
        for i in range(n_source):
            s = s + cws[i]

        model = []
        for p in pulsars:
            model.append(s(p))
        ptas.append(signal_base.PTA(model))

    for i, PTA in enumerate(ptas):
        print(i)
        print(PTA.params)
        #print(PTA.summary())

    #getting the number of dimensions
    #ndim = len(pta.params)

    #do n_global_first global proposal steps before starting any other step
    n_global_first = 0
    
    #fisher updating every n_fish_update step
    n_fish_update = 200 #50
    #print out status every n_status_update step
    n_status_update = 10
    #add current sample to de history file every n_de_history step
    n_de_history = 10

    #array to hold Differential Evolution history
    history_size = 1000    
    de_history = np.zeros((n_chain, history_size, n_source*7+1))
    #start DE after de_start_iter iterations
    de_start_iter = 100

    #setting up temperature ladder (geometric spacing)
    c = T_max**(1.0/(n_chain-1))
    Ts = c**np.arange(n_chain)
    print("Using {0} temperature chains with a geometric spacing of {1:.3f}.\
 Temperature ladder is:\n".format(n_chain,c),Ts)

    #setting up array for the fisher eigenvalues
    eig = np.ones((n_chain, max_n_source, 7, 7))*0.1

    #setting up array for the samples and filling first sample with random draw
    samples = np.zeros((n_chain, N, max_n_source*7+1))
    for j in range(n_chain):
        n_source = np.random.choice(max_n_source) + 1
        samples[j,0,0] = n_source
        print(n_source)
        #TODO maybe start from an Fe-proposed point?
        samples[j,0,1:n_source*7+1] = np.hstack(p.sample() for p in ptas[n_source-1].params)
        samples[j,0,n_source*7+1:] = np.zeros((max_n_source-n_source)*7)
        #samples[j,0,1:] = np.array([0.5, -0.5, 0.5403, 0.8776, 4.5, 3.5, -8.0969, -7.3979, -13.4133, -12.8381, 1.0, 0.5, 1.0, 0.5])
        #samples[j,0,1:] = np.array([0.0, 0.54, 1.0, -8.0, -13.39, 2.0, 0.5])
    print(samples[0,0,:])

    #setting up arrays to record acceptance and swaps
    a_yes=np.zeros(n_chain+1)
    a_no=np.zeros(n_chain+1)
    swap_record=[]

    #set up probabilities of different proposals
    total_weight = (regular_weight + PT_swap_weight + Fe_proposal_weight + 
                    draw_from_prior_weight + de_weight + RJ_weight)
    swap_probability = PT_swap_weight/total_weight
    fe_proposal_probability = Fe_proposal_weight/total_weight
    regular_probability = regular_weight/total_weight
    draw_from_prior_probability = draw_from_prior_weight/total_weight
    de_probability = de_weight/total_weight
    RJ_probability = RJ_weight/total_weight
    print("Percentage of steps doing different jumps:\nPT swaps: {0:.2f}%\nRJ moves: {5:.2f}%\n\
Fe-proposals: {1:.2f}%\nJumps along Fisher eigendirections: {2:.2f}%\n\
Draw from prior: {3:.2f}%\nDifferential evolution jump: {4:.2f}%".format(swap_probability*100,
          fe_proposal_probability*100, regular_probability*100, draw_from_prior_probability*100,
          de_probability*100, RJ_probability*100))

    for i in range(int(N-1)):
        #add current sample to DE history
        if i%n_de_history==0 and i>=de_start_iter:
            de_hist_index = int((i-de_start_iter)/n_de_history)%history_size
            de_history[:,de_hist_index,:] = samples[:,i,:]
        #print out run state every 10 iterations
        if i%n_status_update==0:
            acc_fraction = a_yes/(a_no+a_yes)
            print('Progress: {0:2.2f}% '.format(i/N*100) +
                  'Acceptance fraction (swap, each chain): ({0:1.2f} '.format(acc_fraction[0]) +
                  ' '.join([',{{{}:1.2f}}'.format(i) for i in range(n_chain)]).format(*acc_fraction[1:]) +
                  ')' + '\r',end='')
        #update our eigenvectors from the fisher matrix every 100 iterations
        if i%n_fish_update==0 and i>=n_global_first:
            #only update T>1 chains every 10th time
            if i%(n_fish_update*10)==0:
                for j in range(n_chain):
                    n_source = int(np.copy(samples[j,i,0]))
                    #print(n_source)
                    eigenvectors = get_fisher_eigenvectors(samples[j,i,1:n_source*7+1], ptas[n_source-1], T_chain=Ts[j], n_source=n_source)
                    #check if eigenvector calculation was succesful
                    #if not, we just keep the initializes eig full of 0.1 values
                    if np.all(eigenvectors):
                        eig[j,:n_source,:,:] = eigenvectors
            else:
                n_source = int(np.copy(samples[0,i,0]))
                eigenvectors = get_fisher_eigenvectors(samples[0,i,1:n_source*7+1], ptas[n_source-1], T_chain=Ts[0], n_source=n_source)
                #check if eigenvector calculation was succesful
                #if not, we just keep the initializes eig full of 0.1 values              
                if np.all(eigenvectors):
                    eig[0,:n_source,:,:] = eigenvectors
            #print(eig)
        if i<n_global_first:
            do_fe_global_jump(n_chain, max_n_source, ptas, samples, i, Ts, a_yes, a_no, fe_file)
        else:
            #draw a random number to decide which jump to do
            jump_decide = np.random.uniform()
            #PT swap move
            if jump_decide<swap_probability:
                #print("swap")
                do_pt_swap(n_chain, max_n_source, ptas, samples, i, Ts, a_yes, a_no, swap_record)
            #global proposal based on Fe-statistic
            elif jump_decide<swap_probability+fe_proposal_probability:
                #print("Fe")
                do_fe_global_jump(n_chain, max_n_source, ptas, samples, i, Ts, a_yes, a_no, fe_file)
            #draw from prior move
            elif jump_decide<swap_probability+fe_proposal_probability+draw_from_prior_probability:
                do_draw_from_prior_move(n_chain, n_source, pta, samples, i, Ts, a_yes, a_no)
            #do DE jump
            elif (jump_decide<swap_probability+fe_proposal_probability+
                 draw_from_prior_probability+de_probability and i>=de_start_iter):
                do_de_jump(n_chain, ndim, pta, samples, i, Ts, a_yes, a_no, de_history)
            #do RJ move
            elif (jump_decide<swap_probability+fe_proposal_probability+
                 draw_from_prior_probability+de_probability+RJ_probability):
                do_rj_move(n_chain, max_n_source, ptas, samples, i, Ts, a_yes, a_no, fe_file)
            #regular step
            else:
                #print("fisher")
                regular_jump(n_chain, max_n_source, ptas, samples, i, Ts, a_yes, a_no, eig)
            #print(samples[0,i,:])
    acc_fraction = a_yes/(a_no+a_yes)
    return samples, acc_fraction, swap_record


################################################################################
#
#REVERSIBLE-JUMP (RJ, aka TRANS-DIMENSIONAL) MOVE
#
################################################################################
def do_rj_move(n_chain, max_n_source, ptas, samples, i, Ts, a_yes, a_no, fe_file):
    for j in range(n_chain):
        n_source = int(np.copy(samples[j,i,0]))
        
        add_prob = 0.5 #flat prior on n_source-->same propability of addind and removing
        #decide if we add or remove a signal
        direction_decide = np.random.uniform()
        if direction_decide<add_prob and n_source!=max_n_source: #adding a signal------------------------------------------------------
            if fe_file==None:
                raise Exception("Fe-statistics data file is needed for Fe global propsals")
            npzfile = np.load(fe_file)
            freqs = npzfile['freqs']
            fe = npzfile['fe']
            inc_max = npzfile['inc_max']
            psi_max = npzfile['psi_max']
            phase0_max = npzfile['phase0_max']
            h_max = npzfile['h_max']
   
            alpha = 0.1
 
            #set limit used for rejection sampling below
            fe_limit = np.max(fe)
            #if the max is too high, cap it at Fe=200 (Neil's trick to not to be too restrictive)
            #if fe_limit>200:
            #    fe_limit=200
    
            accepted = False
            while accepted==False:
                f_new = 10**(ptas[-1].params[3].sample())
                f_idx = (np.abs(freqs - f_new)).argmin()

                gw_theta = np.arccos(ptas[-1].params[0].sample())
                gw_phi = ptas[-1].params[2].sample()
                hp_idx = hp.ang2pix(hp.get_nside(fe), gw_theta, gw_phi)

                fe_new_point = fe[f_idx, hp_idx]
                if np.random.uniform()<(fe_new_point/fe_limit):
                    accepted = True
            #if j==0: print("f={0} Hz; (theta,phi)=({1},{2})".format(f_new, gw_theta, gw_phi))

            cos_inc = np.cos(inc_max[f_idx, hp_idx]) + 2*alpha*(np.random.uniform()-0.5)
            psi = psi_max[f_idx, hp_idx] + 2*alpha*(np.random.uniform()-0.5)
            phase0 = phase0_max[f_idx, hp_idx] + 2*alpha*(np.random.uniform()-0.5)
            log10_h = np.log10(h_max[f_idx, hp_idx]) + 2*alpha*(np.random.uniform()-0.5)

            new_source = np.array([np.cos(gw_theta), cos_inc, gw_phi, np.log10(f_new), log10_h, phase0, psi])
            new_point = np.copy(samples[j,i,1:(n_source+1)*7+1])
            new_point[n_source*7:(n_source+1)*7] = new_source
            #if j==0: print("Adding")
            #if j==0: print(samples[j,i,1:n_source*7+1], new_point)

            log_acc_ratio = ptas[(n_source+1)-1].get_lnlikelihood(new_point)
            log_acc_ratio += ptas[(n_source+1)-1].get_lnprior(new_point)
            log_acc_ratio += -ptas[n_source-1].get_lnlikelihood(samples[j,i,1:n_source*7+1])
            log_acc_ratio += -ptas[n_source-1].get_lnprior(samples[j,i,1:n_source*7+1])

            acc_ratio = np.exp(log_acc_ratio)**(1/Ts[j])
            #if j==0: print(acc_ratio)
            if np.random.uniform()<=acc_ratio:
                #if j==0: print("Pafff")
                samples[j,i+1,0] = n_source+1
                samples[j,i+1,1:(n_source+1)*7+1] = new_point
            else:
                samples[j,i+1,0] = n_source
                samples[j,i+1,1:n_source*7+1] = samples[j,i,1:n_source*7+1]

           
        elif direction_decide>add_prob and n_source!=1:   #removing a signal----------------------------------------------------------
            #choose which source to remove
            remove_index = np.random.randint(n_source)
            new_point = np.delete(samples[j,i,1:n_source*7+1], range(remove_index*7,(remove_index+1)*7))
            #if j==0: print("Removing")
            #if j==0: print(remove_index)
            #if j==0: print(samples[j,i,1:n_source*7+1], new_point)
            
            log_acc_ratio = ptas[(n_source-1)-1].get_lnlikelihood(new_point)
            log_acc_ratio += ptas[(n_source-1)-1].get_lnprior(new_point)
            log_acc_ratio += -ptas[n_source-1].get_lnlikelihood(samples[j,i,1:n_source*7+1])
            log_acc_ratio += -ptas[n_source-1].get_lnprior(samples[j,i,1:n_source*7+1])
            
            acc_ratio = np.exp(log_acc_ratio)**(1/Ts[j])
            #if j==0: print(acc_ratio)
            if np.random.uniform()<=acc_ratio:
                #if j==0: print("Wuuuuuh")
                samples[j,i+1,0] = n_source-1
                samples[j,i+1,1:(n_source-1)*7+1] = new_point
            else:
                samples[j,i+1,0] = n_source
                samples[j,i+1,1:n_source*7+1] = samples[j,i,1:n_source*7+1]

        else: #we selected adding when we are at max_n_source or removing the only signal we have, so we will just skip this step
            #if j==0: print("Skippy")
            samples[j,i+1,:] = samples[j,i,:]
         

################################################################################
#
#DIFFERENTIAL EVOLUTION PROPOSAL
#
################################################################################

def do_de_jump(n_chain, n_source, pta, samples, i, Ts, a_yes, a_no, de_history):
    de_indices = np.random.choice(de_history.shape[1], size=2, replace=False)

    #TODO: make it work for changing dimensions!!!
    ndim = 7*n_source

    #setting up our two x arrays and replace them with a random draw if the
    #have not been filled up yet with history
    x1 = de_history[:,de_indices[0],:]
    if np.array_equal(x1, np.zeros((n_chain, ndim))):
        for j in range(n_chain):
            x1[j,:] = np.hstack(p.sample() for p in pta.params)
    
    x2 = de_history[:,de_indices[1],:]
    if np.array_equal(x2, np.zeros((n_chain, ndim))):
        for j in range(n_chain):
            x2[j,:] = np.hstack(p.sample() for p in pta.params)

    alpha = 1.0
    if np.random.uniform()<0.9:
        alpha = np.random.normal(scale=2.38/np.sqrt(2*ndim))

    for j in range(n_chain):
        new_point = samples[j,i,:] + alpha*(x1[j,:]-x2[j,:])
        
        log_acc_ratio = pta.get_lnlikelihood(new_point[:])
        log_acc_ratio += pta.get_lnprior(new_point[:])
        log_acc_ratio += -pta.get_lnlikelihood(samples[j,i,:])
        log_acc_ratio += -pta.get_lnprior(samples[j,i,:])

        acc_ratio = np.exp(log_acc_ratio)**(1/Ts[j])
        if np.random.uniform()<=acc_ratio:
            for k in range(ndim):
                samples[j,i+1,k] = new_point[k]
            a_yes[j+1]+=1
        else:
            for k in range(ndim):
                samples[j,i+1,k] = samples[j,i,k]
            a_no[j+1]+=1


################################################################################
#
#DRAW FROM PRIOR MOVE
#
################################################################################

def do_draw_from_prior_move(n_chain, n_source, pta, samples, i, Ts, a_yes, a_no):
    #TODO: make it work for changing dimensions!!!
    ndim = n_source*7
    for j in range(n_chain):
        #make a rendom draw from the prior
        new_point = np.hstack(p.sample() for p in pta.params)

        #calculate acceptance ratio
        log_acc_ratio = pta.get_lnlikelihood(new_point[:])
        log_acc_ratio += pta.get_lnprior(new_point[:])
        log_acc_ratio += -pta.get_lnlikelihood(samples[j,i,1:])
        log_acc_ratio += -pta.get_lnprior(samples[j,i,1:])
        
        acc_ratio = np.exp(log_acc_ratio)**(1/Ts[j])
        samples[j,i+1,0] = n_source
        if np.random.uniform()<=acc_ratio:
            for k in range(ndim):
                samples[j,i+1,k+1] = new_point[k]
            a_yes[j+1]+=1
        else:
            for k in range(ndim):
                samples[j,i+1,k+1] = samples[j,i,k+1]
            a_no[j+1]+=1

################################################################################
#
#GLOBAL PROPOSAL BASED ON FE-STATISTIC
#
################################################################################

def do_fe_global_jump(n_chain, max_n_source, ptas, samples, i, Ts, a_yes, a_no, fe_file):    
    if fe_file==None:
        raise Exception("Fe-statistics data file is needed for Fe global propsals")
    npzfile = np.load(fe_file)
    freqs = npzfile['freqs']
    fe = npzfile['fe']
    inc_max = npzfile['inc_max']
    psi_max = npzfile['psi_max']
    phase0_max = npzfile['phase0_max']
    h_max = npzfile['h_max']

    #ndim = n_source*7

    #set probability of deterministic vs flat proposal in extrinsic parameters
    p_det = 0.5
    #set width of deterministic proposal
    alpha = 0.1

    #print("Global proposal properties: p_det={0}, width={1}".format(p_det,alpha))

    #set limit used for rejection sampling below
    fe_limit = np.max(fe)
    #if the max is too high, cap it at Fe=200 (Neil's trick to not to be too restrictive)
    #if fe_limit>200:
    #    fe_limit=200
    
    for j in range(n_chain):
        accepted = False
        while accepted==False:
            f_new = 10**(ptas[-1].params[3].sample())
            f_idx = (np.abs(freqs - f_new)).argmin()

            gw_theta = np.arccos(ptas[-1].params[0].sample())
            gw_phi = ptas[-1].params[2].sample()
            hp_idx = hp.ang2pix(hp.get_nside(fe), gw_theta, gw_phi)

            fe_new_point = fe[f_idx, hp_idx]
            if np.random.uniform()<(fe_new_point/fe_limit):
                accepted = True
        #if j==0: print("f={0} Hz; (theta,phi)=({1},{2})".format(f_new, gw_theta, gw_phi))

        if np.random.uniform()<p_det:
            deterministic=True
        else:
            deterministic=False

        if deterministic:
            cos_inc = np.cos(inc_max[f_idx, hp_idx]) + 2*alpha*(np.random.uniform()-0.5)
            psi = psi_max[f_idx, hp_idx] + 2*alpha*(np.random.uniform()-0.5)
            phase0 = phase0_max[f_idx, hp_idx] + 2*alpha*(np.random.uniform()-0.5)
            log10_h = np.log10(h_max[f_idx, hp_idx]) + 2*alpha*(np.random.uniform()-0.5)
        else:
            cos_inc = ptas[-1].params[1].sample()
            psi = ptas[-1].params[6].sample()
            phase0 = ptas[-1].params[5].sample()
            log10_h = ptas[-1].params[4].sample()

        #print("hah")
        #if j==0: print(pta.get_lnlikelihood(samples[j,i,1:]))
        #choose randomly which source to change
        n_source = int(np.copy(samples[j,i,0]))
        source_select = np.random.randint(n_source)
        new_point = np.copy(samples[j,i,1:n_source*7+1])
        new_point[source_select*7:(source_select+1)*7] = np.array([np.cos(gw_theta), cos_inc, gw_phi, np.log10(f_new),
                                                                               log10_h, phase0, psi])
        
        #print(source_select)
        #print(samples[j,i,:])
        #print(new_point)
        #if j==0: print("----------------------------------")
        #if j==0:
        #    print('-'*30)
        #    print(deterministic)
        #    print(source_select)
        #    print(samples[j,i,1:])
        #    print(new_point)
        #    print(pta.get_lnlikelihood(new_point))
        #    print(pta.get_lnlikelihood(samples[j,i,1:]))
        #    print(pta.get_lnlikelihood(new_point[:]), pta.get_lnlikelihood(samples[j,i,1:]))
        #print(pta.get_lnlikelihood(new_point))
        #print(pta.get_lnlikelihood(samples[j,i,1:]))
        #test_array = np.array([  0.03716533,   0.36221859,   0.25842528,   0.51300182,   3.51393577,
   #4.74312836,  -8.04418148,  -8.08967996, -13.32766946, -13.51676111,
   #1.23215406,   1.13994404,   1.16368098,   0.34977929])
        #test_likelihood = pta.get_lnlikelihood(test_array)
        #print(pta.get_lnlikelihood(new_point))
        #print(pta.get_lnlikelihood(samples[j,i,1:]))

        if fe_new_point>fe_limit:
            fe_new_point=fe_limit        
        #if j==0: print("Parts of log_acc ratio")
        log_acc_ratio = ptas[n_source-1].get_lnlikelihood(new_point)
        #if j==0:
        #    print(pta.get_lnlikelihood(new_point))
        #    print(log_acc_ratio)
        log_acc_ratio += ptas[n_source-1].get_lnprior(new_point)
        #if j==0:
        #    print(pta.get_lnprior(new_point))
        #    print(log_acc_ratio)
        log_acc_ratio += -ptas[n_source-1].get_lnlikelihood(samples[j,i,1:])
        #if j==0:
        #    print(-pta.get_lnlikelihood(samples[j,i,1:]))
        #    print(log_acc_ratio)
        log_acc_ratio += -ptas[n_source-1].get_lnprior(samples[j,i,1:])
        #if j==0:
        #    print(-pta.get_lnprior(samples[j,i,1:]))
        #    print(log_acc_ratio)

        #get ratio of proposal density for the Hastings ratio
        f_old = 10**samples[j,i,1+3+source_select*7]
        f_idx_old = (np.abs(freqs - f_old)).argmin()

        gw_theta_old = np.arccos(samples[j,i,1+source_select*7])
        gw_phi_old = samples[j,i,1+2+source_select*7]
        hp_idx_old = hp.ang2pix(hp.get_nside(fe), gw_theta_old, gw_phi_old)
        #print(f_old, gw_theta_old, gw_phi_old)
        
        fe_old_point = fe[f_idx_old, hp_idx_old]
        if fe_old_point>fe_limit:
            fe_old_point = fe_limit

        cos_inc_old = np.cos(inc_max[f_idx_old, hp_idx_old])
        psi_old = psi_max[f_idx_old, hp_idx_old]
        phase0_old = phase0_max[f_idx_old, hp_idx_old]
        log10_h_old = np.log10(h_max[f_idx_old, hp_idx_old])
        
        old_params_fe = [cos_inc_old, log10_h_old, phase0_old, psi_old]
        #TODO:extract prior ranges from pta object!!!!
        prior_ranges = [2.0, 7.0, 2.0*np.pi, np.pi]
        
        new_params = [cos_inc, log10_h, phase0, psi]
        new_params_fe = [np.cos(inc_max[f_idx, hp_idx]), np.log10(h_max[f_idx, hp_idx]),
                        phase0_max[f_idx, hp_idx], psi_max[f_idx, hp_idx]]
        
        hastings_extra_factor=1.0
        for k, prior_range, old_param_fe, new_param, new_param_fe in zip([1,4,5,6], prior_ranges, old_params_fe, new_params, new_params_fe):
            old_param = samples[j,i,1+k+source_select*7]
            #True if the ith sample was at a place where we could jump with a deterministic jump
            #False otherwise            
            det_old = np.abs(old_param-old_param_fe)<alpha
            det_new = np.abs(new_param-new_param_fe)<alpha
            if det_new and not det_old:
                #if j==0: print("From non-det to det")
                hastings_extra_factor *= 1.0/( p_det/(1-p_det)*prior_range/(2*alpha) + 1 )
            elif not det_new and det_old:
                #if j==0: print("From det to non-det")
                hastings_extra_factor *= p_det/(1-p_det)*prior_range/(2*alpha) + 1

        
        #if j==0:
        #    print("i={0}".format(i))
        #    print("L-ratio={0}".format(np.exp(log_acc_ratio)))
        #    print("Fe-ratio={0}".format(fe_old_point/fe_new_point))
        #    print("Extra factor={0}".format(hastings_extra_factor))

        acc_ratio = np.exp(log_acc_ratio)**(1/Ts[j])*(fe_old_point/fe_new_point)*hastings_extra_factor
        #if j==0: print(acc_ratio)
        samples[j,i+1,0] = n_source
        samples[j,i+1,n_source*7+1:] = np.zeros((max_n_source-n_source)*7)
        if np.random.uniform()<=acc_ratio:
            #if j==0:
            #    print('yeeeh')
            #    if not deterministic: print('Ohh jeez')
            samples[j,i+1,1:n_source*7+1] = new_point
            a_yes[j+1]+=1
        else:
            samples[j,i+1,1:n_source*7+1] = samples[j,i,1:n_source*7+1]
            a_no[j+1]+=1
    

################################################################################
#
#REGULAR MCMC JUMP ROUTINE (JUMPING ALONG EIGENDIRECTIONS)
#
################################################################################

def regular_jump(n_chain, max_n_source, ptas, samples, i, Ts, a_yes, a_no, eig):
    #print("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeigen")
    for j in range(n_chain):
        n_source = int(np.copy(samples[j,i,0]))
        ndim = n_source*7
        source_select = np.random.randint(n_source)
        jump_select = np.random.randint(int(ndim/n_source))
        jump_1source = eig[j,source_select,jump_select,:]
        #jump = np.array([jump_1source[int(i/n_source)] if i%n_source==source_select else 0.0 for i in range(ndim)])
        jump = np.array([jump_1source[int(i-source_select*7)] if i>=source_select*7 and i<(source_select+1)*7 else 0.0 for i in range(ndim)])

        new_point = samples[j,i,1:n_source*7+1] + jump*np.random.normal()
        #if j==0:        
        #    print('-'*20)
        #    print("i={0}".format(i))
        #    print(source_select)
        #    print(jump)        
        #    print(samples[j,i,:])
        #    print(new_point)

        log_acc_ratio = ptas[n_source-1].get_lnlikelihood(new_point)
        log_acc_ratio += ptas[n_source-1].get_lnprior(new_point)
        log_acc_ratio += -ptas[n_source-1].get_lnlikelihood(samples[j,i,1:])
        log_acc_ratio += -ptas[n_source-1].get_lnprior(samples[j,i,1:])

        acc_ratio = np.exp(log_acc_ratio)**(1/Ts[j])
        #if j==0: print("L-ratio(fisher)={0}".format(acc_ratio))
        samples[j,i+1,0] = n_source
        #TODO: check if we need the next line (I think samples is already filled with zeros)
        #If not, remove it from here and from other jump functions too
        samples[j,i+1,n_source*7+1:] = np.zeros((max_n_source-n_source)*7)
        if np.random.uniform()<=acc_ratio:
            #if j==0: print("Yupiiiiii")
            samples[j,i+1,1:n_source*7+1] = new_point
            #for k in range(ndim):
            #    samples[j,i+1,k+1] = new_point[k]
            a_yes[j+1]+=1
        else:
            samples[j,i+1,1:n_source*7+1] = samples[j,i,1:n_source*7+1]
            #for k in range(ndim):
            #    samples[j,i+1,k+1] = samples[j,i,k+1]
            a_no[j+1]+=1

################################################################################
#
#PARALLEL TEMPERING SWAP JUMP ROUTINE
#
################################################################################
def do_pt_swap(n_chain, max_n_source, ptas, samples, i, Ts, a_yes, a_no, swap_record):
    #ndim=n_source*7
    
    swap_chain = np.random.randint(n_chain-1)

    n_source1 = int(np.copy(samples[swap_chain,i,0]))
    n_source2 = int(np.copy(samples[swap_chain+1,i,0]))

    #print("-"*30)

    #print(samples[swap_chain,i,1:])
    #print(samples[swap_chain+1,i,1:])

    log_acc_ratio = -ptas[n_source1-1].get_lnlikelihood(samples[swap_chain,i,1:n_source1*7+1]) / Ts[swap_chain]
    log_acc_ratio += -ptas[n_source1-1].get_lnprior(samples[swap_chain,i,1:n_source1*7+1]) / Ts[swap_chain]
    log_acc_ratio += -ptas[n_source2-1].get_lnlikelihood(samples[swap_chain+1,i,1:n_source2*7+1]) / Ts[swap_chain+1]
    log_acc_ratio += -ptas[n_source2-1].get_lnprior(samples[swap_chain+1,i,1:n_source2*7+1]) / Ts[swap_chain+1]
    log_acc_ratio += ptas[n_source2-1].get_lnlikelihood(samples[swap_chain+1,i,1:n_source2*7+1]) / Ts[swap_chain]
    log_acc_ratio += ptas[n_source2-1].get_lnprior(samples[swap_chain+1,i,1:n_source2*7+1]) / Ts[swap_chain]
    log_acc_ratio += ptas[n_source1-1].get_lnlikelihood(samples[swap_chain,i,1:n_source1*7+1]) / Ts[swap_chain+1]
    log_acc_ratio += ptas[n_source1-1].get_lnprior(samples[swap_chain,i,1:n_source1*7+1]) / Ts[swap_chain+1]

    #samples[:,i+1,0] = n_source

    acc_ratio = np.exp(log_acc_ratio)
    #print(i)
    #print("L-ratio(PT)={0}".format(acc_ratio))
    #print(n_source1, n_source2)
    #print("Swap: {0}".format(swap_chain))
    if np.random.uniform()<=acc_ratio:
        #print("Woooow")
        for j in range(n_chain):
            if j==swap_chain:
                samples[j,i+1,:] = samples[j+1,i,:]
            elif j==swap_chain+1:
                samples[j,i+1,:] = samples[j-1,i,:]
            else:
                samples[j,i+1,:] = samples[j,i,:]
        a_yes[0]+=1
        swap_record.append(swap_chain)
    else:
        for j in range(n_chain):
            samples[j,i+1,:] = samples[j,i,:]
        a_no[0]+=1

################################################################################
#
#FISHER EIGENVALUE CALCULATION
#
################################################################################
def get_fisher_eigenvectors(params, pta, T_chain=1, epsilon=1e-4, n_source=1):
    #get dimension and set up an array for the fisher matrix    
    dim = int(params.shape[0]/n_source)
    fisher = np.zeros((n_source,dim,dim))
    eig = []

    #print(params)

    #lnlikelihood at specified point
    nn = pta.get_lnlikelihood(params)
    
    
    for k in range(n_source):
        #print(k)
        #calculate diagonal elements
        for i in range(dim):
            #create parameter vectors with +-epsilon in the ith component
            paramsPP = np.copy(params)
            paramsMM = np.copy(params)
            paramsPP[i+k*dim] += 2*epsilon
            paramsMM[i+k*dim] -= 2*epsilon
            #print(paramsPP)
            
            #lnlikelihood at +-epsilon positions
            pp = pta.get_lnlikelihood(paramsPP)
            mm = pta.get_lnlikelihood(paramsMM)

            #print(pp, nn, mm)
            
            #calculate diagonal elements of the Hessian from a central finite element scheme
            #note the minus sign compared to the regular Hessian
            fisher[k,i,i] = -(pp - 2.0*nn + mm)/(4.0*epsilon*epsilon)

        #calculate off-diagonal elements
        for i in range(dim):
            for j in range(i+1,dim):
                #create parameter vectors with ++, --, +-, -+ epsilon in the ith and jth component
                paramsPP = np.copy(params)
                paramsMM = np.copy(params)
                paramsPM = np.copy(params)
                paramsMP = np.copy(params)

                paramsPP[i+k*dim] += epsilon
                paramsPP[j+k*dim] += epsilon
                paramsMM[i+k*dim] -= epsilon
                paramsMM[j+k*dim] -= epsilon
                paramsPM[i+k*dim] += epsilon
                paramsPM[j+k*dim] -= epsilon
                paramsMP[i+k*dim] -= epsilon
                paramsMP[j+k*dim] += epsilon

                #lnlikelihood at those positions
                pp = pta.get_lnlikelihood(paramsPP)
                mm = pta.get_lnlikelihood(paramsMM)
                pm = pta.get_lnlikelihood(paramsPM)
                mp = pta.get_lnlikelihood(paramsMP)

                #calculate off-diagonal elements of the Hessian from a central finite element scheme
                #note the minus sign compared to the regular Hessian
                fisher[k,i,j] = -(pp - mp - pm + mm)/(4.0*epsilon*epsilon)
                fisher[k,j,i] = -(pp - mp - pm + mm)/(4.0*epsilon*epsilon)
        
        #print(fisher)
        #correct for the given temperature of the chain    
        fisher = fisher/T_chain
      
        try:
            #Filter nans and infs and replace them with 1s
            #this will imply that we will set the eigenvalue to 100 a few lines below
            FISHER = np.where(np.isfinite(fisher[k,:,:]), fisher[k,:,:], 1.0)
            if not np.array_equal(FISHER, fisher[k,:,:]):
                print("Changed some nan elements in the Fisher matrix to 1.0")

            #Find eigenvalues and eigenvectors of the Fisher matrix
            w, v = np.linalg.eig(FISHER)

            #filter w for eigenvalues smaller than 100 and set those to 100 -- Neil's trick
            eig_limit = 100.0    
            W = np.where(np.abs(w)>eig_limit, w, eig_limit)
            #print(W)
            #print(np.sum(v**2, axis=0))
            #if T_chain==1.0: print(W)
            #if T_chain==1.0: print(v)

            eig.append( (np.sqrt(1.0/np.abs(W))*v).T )
            #print(np.sum(eig**2, axis=1))
            #if T_chain==1.0: print(eig)

        except:
            print("An Error occured in the eigenvalue calculation")
            eig.append( np.array(False) )

        #import matplotlib.pyplot as plt
        #plt.figure()
        #plt.imshow(np.log10(np.abs(np.real(np.array(FISHER)))))
        #plt.colorbar()
    
    return np.array(eig)

################################################################################
#
#MAKE AN ARRAY CONTAINING GLOBAL PROPOSAL DENSITY FROM F_E-STATISTICS
#
################################################################################
def make_fe_global_proposal(fe_func, f_min=1e-9, f_max=1e-7, n_freq=400,
                            NSIDE=8, maximized_parameters=False):
    m = np.zeros((n_freq, hp.nside2npix(NSIDE)))
    if maximized_parameters:
        inc_max = np.zeros((n_freq, hp.nside2npix(NSIDE)))
        psi_max = np.zeros((n_freq, hp.nside2npix(NSIDE)))
        phase0_max = np.zeros((n_freq, hp.nside2npix(NSIDE)))
        h_max = np.zeros((n_freq, hp.nside2npix(NSIDE)))

    freqs = np.logspace(np.log10(f_min), np.log10(f_max), n_freq)

    idx = np.arange(hp.nside2npix(NSIDE))
    for i, f in enumerate(freqs):
        print("{0}th freq out of {1}".format(i, n_freq))
        if maximized_parameters:
            m[i,:], inc_max[i,:], psi_max[i,:], phase0_max[i,:], h_max[i,:] = fe_func(f,
                                np.array(hp.pix2ang(NSIDE, idx)),
                                maximized_parameters=maximized_parameters)
        else:
            m[i,:] = fe_func(f, np.array(hp.pix2ang(NSIDE, idx)),
                             maximized_parameters=maximized_parameters)
    if maximized_parameters:
        return freqs, m, inc_max, psi_max, phase0_max, h_max
    else:
        return freqs, m


