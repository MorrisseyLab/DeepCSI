# -*- coding: utf-8 -*-
"""
Created on Thu May 26 17:31:49 2016

@author: edward
"""

import cv2
import numpy as np
from matplotlib import pyplot as plt
from MiscFunctions import plot_img
from MiscFunctions import getIndexesTileImage, plot_histogram
from MiscFunctions import col_deconvol, col_deconvol_32, plot_img
from MiscFunctions import getROI_img_vips, col_deconvol_and_blur
import openslide as osl
from sklearn               import mixture
from cnt_Feature_Functions import filterSmallArea, st_3, plotSegmented
from deconv_mat import deconv_mat_AB, deconv_mat_betaCat, deconv_mat_MAOA, deconv_mat_MPAS
from Clonal_Stains.mPAS_Segment_Clone import get_mPAS_Thresholds

# Sometimes moving to 8bit can lead to a threshold above 255
# also the algorithm will use several thresholds including one that's 10% higher
# So set thresh to 255 - 10% of 255
def adjust_threshold_overshoot(thresh, min_thresh = 231):
    if thresh > min_thresh: 
        thresh = min_thresh
    return thresh        

def plot_histogram_w_vline(x, vline1, vline2, bins=50, norm_it = False):
    hist, bins = np.histogram(x, bins=bins, normed = norm_it)
    width = 0.7 * (bins[1] - bins[0])
    center = (bins[:-1] + bins[1:]) / 2
    plt.bar(center, hist, align='center', width=width)
    plt.axvline(x = vline1, color = "red")        
    plt.axvline(x = vline2, color = "green")        
    plt.show()  

def plot_histogram_no_show(x, bins=50, norm_it = False):
    hist, bins = np.histogram(x, bins=bins, normed = norm_it)
    width = 0.7 * (bins[1] - bins[0])
    center = (bins[:-1] + bins[1:]) / 2
    plt.bar(center, hist, align='center', width=width)


def plotSizeDistribution(mean_x, prec_x, coef_mix, x):
    x_range     = np.arange(np.min(x), np.max(x), np.max(x)/1000)
    const_val   = -0.5*np.log(2.*np.pi)    
    log_lik_vec = const_val + 0.5*np.log(prec_x) - 0.5*prec_x*(x_range - mean_x)**2
    y_gauss     = coef_mix*np.exp(log_lik_vec)
    plt.plot(x_range, y_gauss, color = "r", linestyle = '--', linewidth=2)

## =============================================================
def getEM_threshold_One(th_img, subSample = 50000,  plot_me = False, min_covar = 0.01, num_comp = 3):
    # Subsample data 
    points    = np.random.choice(th_img.ravel(), size=(subSample,1), replace=False, p=None)
    points    = np.sort(points, 0).astype('float', copy=False)    
    points    = points[points[:,0]<240,:] # Remove very high values
    if (not th_img.dtype=='uint8'):
        points    = points[points[:,0] < (240./255.),:] # Remove very high values
    gmm = mixture.GaussianMixture(num_comp, reg_covar = min_covar).fit(points)
    if num_comp == 1:
        return -999, gmm
    comp_use  = np.argsort(gmm.means_[:,0])[-2]
    ## Find cut off  (avoid left hand tails behavour using first predicted "label = 1" after mean of first gaussian)
    indx_aux    = points > gmm.means_[comp_use]
    points_sub  = points[indx_aux].reshape(-1, 1)
    label_pred  = gmm.predict(points_sub)    
    
    if (np.sum(np.diff(label_pred))==0):
        return (-999, gmm)
    indx_thresh = np.where(np.diff(label_pred))[0][0]
    thresh_em   = points_sub[indx_thresh]

    if plot_me:
        plot_histogram_no_show(points, bins=70, norm_it = True)
        for i in range(num_comp):  
            prec_x_i = 1./gmm.covariances_[i][0]
            mean_x_i = gmm.means_[i][0]
            coef_mix = gmm.weights_[i]
            plotSizeDistribution(mean_x_i, prec_x_i, coef_mix, points)
        plt.axvline(x=thresh_em, color = "red")        
        plt.title('Mixture model')
        plt.show()  

    return(thresh_em, gmm)

def auto_choose_ROI(file_name, deconv_mat, plot_images = False):
    # Access slide file through osl to get metadata
    slide       = osl.OpenSlide(file_name)
    ## Find level of reasonable size to plot
    dims_slides = slide.level_dimensions #np.asarray(slide.level_dimensions)
    smallImage  = len(dims_slides) - 1 
    scalingVals = slide.level_downsamples[-1]
    
    img_zoom   = getROI_img_vips(file_name, (0,0), slide.level_dimensions[smallImage], level = smallImage)
    # Plot?
    if plot_images:
        plot_img(img_zoom, hold_plot=True)
    
    img_deconv  = col_deconvol(img_zoom, deconv_mat)
    
    blurred_img = cv2.GaussianBlur(  img_deconv[:,:,0], ( 7, 7), 0)
    
    # Choose threshold from small image, get tissue outline (ie foreground)
    thresh_EM, _, _    = calculate_thresholds(img_zoom, deconv_mat)   #getEM_threshold_One(blurred_img, subSample = 100000, plot_me = plot_images, min_covar = 1) #200000
    _, img_nucl3 = cv2.threshold( blurred_img[:,:], thresh_EM, 255, cv2.THRESH_BINARY)
    
    # Plot?
    if plot_images:
        plot_img((img_nucl3, blurred_img), hold_plot = True, nameWindow = 'Plots2')
    
    foreground      = cv2.morphologyEx(   img_nucl3,   cv2.MORPH_OPEN,    st_3, iterations =  1) # st_5, iterations = 50)  
    foreground2     = cv2.morphologyEx(  foreground,  cv2.MORPH_CLOSE,    st_3, iterations =  10)
    foreground3     = cv2.morphologyEx( foreground2,   cv2.MORPH_OPEN,    st_3, iterations =  2)
    foreground4     = cv2.morphologyEx( foreground3, cv2.MORPH_DILATE,    st_3, iterations =  3) 
    foreground_filt = filterSmallArea(  foreground4,              1e4)

    ## ================================================================================================================
    # Format is t_ij = (x0, y0, w, h) and arranged in tile_list[i][j] = t_ij
    # Full image ROI
    full_image_ROI = [(0, 0), (foreground_filt.shape[1], foreground_filt.shape[0])]
    tile_list = getIndexesTileImage((foreground_filt.shape[1], foreground_filt.shape[0]), 1., 
                                    full_image_ROI, max_num_pix  = 100)    
    x_tiles    = len(tile_list)
    y_tiles    = len(tile_list[0])
    tile_means = np.zeros((x_tiles, y_tiles))
    for i in range(x_tiles):
        for j in range(y_tiles):
            tile_ij    = tile_list[i][j]
            x1_ij = int(tile_ij[0])
            x2_ij = int(tile_ij[0] + tile_ij[2])
            y1_ij = int(tile_ij[1])
            y2_ij = int(tile_ij[1] + tile_ij[3])
            tile_means[i,j] = np.mean(foreground_filt[y1_ij:y2_ij , x1_ij:x2_ij])
    
    i,j     = np.unravel_index(tile_means.argmax(), tile_means.shape)
    xy_vals = (int(tile_list[i][j][0]), int(tile_list[i][j][1]))
    wh_vals = (int(tile_list[i][j][2]), int(tile_list[i][j][3]))
    
    ## Grid and choose a tile to zoom
    scalingVals = slide.level_downsamples[-1]
    i,j     = np.unravel_index(tile_means.argmax(), tile_means.shape)
    xy_vals = (int(tile_list[i][j][0]*scalingVals), int(tile_list[i][j][1]*scalingVals))
    wh_vals = (int(tile_list[i][j][2]*scalingVals), int(tile_list[i][j][3]*scalingVals))

    return xy_vals, wh_vals

def calculate_thresholds(big_img, deconv_mat):
    ## deconvolution and blurring all done in one by col_deconvol_and_blur()    
    img_deconv32  = col_deconvol_32(big_img, deconv_mat)
    
    blurred_img_nuc       = cv2.GaussianBlur( img_deconv32[:,:,0], (37, 37), 0)
    blurred_img_nuc_small = cv2.GaussianBlur( img_deconv32[:,:,0], (11, 11), 0)
    blurred_img_clone     = cv2.GaussianBlur( img_deconv32[:,:,1], (15, 15), 0)
    
    subsamp_percent = 7
    totpix = blurred_img_nuc.shape[0]*blurred_img_nuc.shape[1]
    subsamplevel = int(totpix*subsamp_percent/100.)
    
    # Choose threshold from small image, get tissue outline (ie foreground) ========================================================
    maxtry = 4
    thresh_EM1 = [-999]
    kk = 0
    while (thresh_EM1[0]==-999 and kk < maxtry):
        thresh_EM1 = getEM_threshold_One(       blurred_img_nuc,    subSample = subsamplevel, plot_me = False, min_covar = 0.000001, num_comp=3)
        kk += 1 
    thresh_EM2 = [-999]
    kk = 0
    while (thresh_EM2[0]==-999 and kk < maxtry):
        thresh_EM2 = getEM_threshold_One( blurred_img_nuc_small,    subSample = subsamplevel, plot_me = False, min_covar = 0.000001, num_comp=3)
        kk += 1 
    thresh_EM3 = [-999]
    kk = 0
    while (thresh_EM3[0]==-999 and kk < maxtry):
        thresh_EM3 = getEM_threshold_One(      blurred_img_clone,    subSample = subsamplevel, plot_me = False, min_covar = 0.000001, num_comp=2)
        kk += 1 
    
    # Check good separation (for crypt markers)
    gauss_vars  = thresh_EM1[1].covariances_[:,0,0]
    gauss_means = thresh_EM1[1].means_[:,0]    
    # If mean + 2sd is larger than mean 2 .. No second dist
    order_use   = np.argsort(gauss_means)
    gauss_vars  = gauss_vars[order_use]
    gauss_means = gauss_means[order_use]

    threepops = True
    two_sig_0   = gauss_means[0]+2.*np.sqrt(gauss_vars[0])
    if two_sig_0 > gauss_means[1]:
        threepops = False
        print("Cannot find three distinct populations for nuclear threshold 1, lowering to two")
        
    two_sig_1   = gauss_means[1]+2*np.sqrt(gauss_vars[1])
    if (two_sig_1 > gauss_means[2] and threepops==True):
        threepops = False
        print("Cannot find three distinct populations for nuclear threshold 1, lowering to two")
        
    if (thresh_EM1[0]==-999 and threepops==True):
        threepops = False
        print("Threshold failure for three populations for nuclear threshold 1, lowering to two")
    
#    if (thresh_EM1[0][0]>0.5 and threepops==True):
#        threepops = False
        
    if (not threepops):
        thresh_EM1old = thresh_EM1 # back-up
        thresh_EM1 = [-999]
        kk = 0
        while (thresh_EM1[0]==-999 and kk < maxtry):
            thresh_EM1 = getEM_threshold_One(       blurred_img_nuc,    subSample = subsamplevel, plot_me = False, min_covar = 0.000001, num_comp=2)        
            kk += 1
        if (thresh_EM1[0]==-999 and not thresh_EM1old[0]==-999):
            print("...However cannot threshold with two components; reverting to three.")
            thresh_EM1 = thresh_EM1old # revert if broken
        if (thresh_EM1[0]==-999 and thresh_EM1old[0]==-999):
            print("...Cannot find threshold! Setting arbitrary value.")
            thresh_EM1 = np.array([0.45]),     
             
    gauss_vars  = thresh_EM2[1].covariances_[:,0,0]
    gauss_means = thresh_EM2[1].means_[:,0]    
    # If mean + 2sd is larger than mean 2 .. No second dist
    order_use   = np.argsort(gauss_means)
    gauss_vars  = gauss_vars[order_use]
    gauss_means = gauss_means[order_use]

    threepops = True
    two_sig_0   = gauss_means[0]+2*np.sqrt(gauss_vars[0])
    if two_sig_0 > gauss_means[1]:
        threepops = False
        print("Cannot find three distinct populations for nuclear threshold 2, lowering to two")
        
    two_sig_1   = gauss_means[1]+2*np.sqrt(gauss_vars[1])
    if (two_sig_1 > gauss_means[2] and threepops==True):
        threepops = False
        print("Cannot find three distinct populations for nuclear threshold 2, lowering to two")
    
    if (thresh_EM2[0]==-999 and threepops==True):
        threepops = False
        print("Threshold failure for three populations for nuclear threshold 2, lowering to two")
   
#    if (thresh_EM2[0][0]>0.5 and threepops==True):
#        threepops = False
        
    if (not threepops):
        thresh_EM2old = thresh_EM2 # back-up
        thresh_EM2 = [-999]
        kk = 0
        while (thresh_EM2[0]==-999 and kk < maxtry):
            thresh_EM2 = getEM_threshold_One( blurred_img_nuc_small,    subSample = subsamplevel, plot_me = False, min_covar = 0.000001, num_comp=2)
            kk += 1
        if (thresh_EM2[0]==-999 and not thresh_EM2old[0]==-999):
            print("...However cannot threshold with two components; reverting to three.")
            thresh_EM2 = thresh_EM2old # revert if broken
        if (thresh_EM2[0]==-999 and thresh_EM2old[0]==-999):
            print("...Cannot find threshold! Setting arbitrary value.")
            thresh_EM2 = np.array([0.45]),
    '''    
    # Check good separation (for clone marker)
    gauss_vars  = thresh_EM3[1].covariances_[:,0,0]
    gauss_means = thresh_EM3[1].means_[:,0]    
    # If mean + 2sd is larger than mean 2 .. No second dist
    order_use   = np.argsort(gauss_means)
    gauss_vars  = gauss_vars[order_use]
    gauss_means = gauss_means[order_use]

    threepops = True
    two_sig_0   = gauss_means[0]+2.*np.sqrt(gauss_vars[0])
    if two_sig_0 > gauss_means[1]:
        threepops = False
        print("Cannot find three distinct populations for clonal threshold, lowering to two")
        
    two_sig_1   = gauss_means[1]+2*np.sqrt(gauss_vars[1])
    if (two_sig_1 > gauss_means[2] and threepops==True):
        threepops = False
        print("Cannot find three distinct populations for clonal threshold, lowering to two")
        
    if (thresh_EM3[0]==-999 and threepops==True):
        threepops = False
        print("Threshold failure for three populations for nuclear threshold 1, lowering to two")

    if (thresh_EM3[0][0]>0.5 and threepops==True):
       threepops = False
        
    if (not threepops):
        thresh_EM3old = thresh_EM3 # back-up
        thresh_EM3 = [-999]
        kk = 0
        while (thresh_EM3[0]==-999 and kk < maxtry):
            thresh_EM3 = getEM_threshold_One(       blurred_img_nuc,    subSample = subsamplevel, plot_me = False, min_covar = 0.000001, num_comp=2)        
            kk += 1
        if (thresh_EM3[0]==-999 and not thresh_EM3old[0]==-999):
            print("...However cannot threshold with two components; reverting to three.")
            thresh_EM3 = thresh_EM3old # revert if broken
        if (thresh_EM3[0]==-999 and thresh_EM3old[0]==-999):
            print("...Cannot find threshold! Setting arbitrary value.")
            thresh_EM3 = np.array([0.4]),
    '''
    gauss_vars  = thresh_EM3[1].covariances_[:,0]
    gauss_means = thresh_EM3[1].means_[:,0]
    
    # If mean + 2sd is larger than mean 2 .. No second dist
    order_use   = np.argsort(gauss_means)
    gauss_vars  = gauss_vars[order_use]
    gauss_means = gauss_means[order_use]
    two_sig_0   = gauss_means[0]+2*np.sqrt(gauss_vars[0])
    

    thresh_new = thresh_EM3[0]
    if two_sig_0 > gauss_means[1]:
        thresh_new = np.percentile(blurred_img_clone, 99.9)  # mean_val + 4*np.sqrt(var_val);thresh_new = thresh_new[0]    
    
    # Outlier threshold
    #thresh_stringent, thresh_lax  = get_mPAS_Thresholds(blurred_img_clone)
        
    #_, img_nucl_blur       = cv2.threshold(       blurred_img_nuc,  thresh_EM1[0], 255, cv2.THRESH_BINARY)
    #_, img_nucl_blur_small = cv2.threshold( blurred_img_nuc_small,  thresh_EM2[0], 255, cv2.THRESH_BINARY)
    #_, stain_thresh        = cv2.threshold(      blurred_img_clone,  thresh_EM3[0], 255, cv2.THRESH_BINARY)
    #_, stain_thresh2       = cv2.threshold(      blurred_img_clone,     thresh_new, 255, cv2.THRESH_BINARY) 
    
    thresh_blur       = int(thresh_EM1[0][0]*255)
    thresh_blur_small = int(thresh_EM2[0][0]*255)
    th_clone          = int(thresh_new*255)
    
    thresh_blur       = adjust_threshold_overshoot(thresh_blur)
    thresh_blur_small = adjust_threshold_overshoot(thresh_blur_small)

    return thresh_blur_small, thresh_blur, th_clone
