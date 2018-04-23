#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Mar 26 15:47:40 2018

@author: doran
"""
import cv2, os, time
import numpy as np
import pyvips
import keras
from keras.preprocessing.image import img_to_array
import DNN.model.u_net as unet
import DNN.params as params
from deconv_mat               import *
from automaticThresh_func     import auto_choose_ROI, calculate_thresholds
from MiscFunctions            import col_deconvol, col_deconvol_and_blur, simplify_contours
from MiscFunctions            import getROI_img_vips, add_offset, write_cnt_text_file
from cnt_Feature_Functions    import joinContoursIfClose_OnlyKeepPatches, st_3
from multicore_morphology     import getForeground_mc
from GUI_ChooseROI_class      import getROI_svs
from Segment_clone_from_crypt import retrieve_clone_nuclear_features, find_clones
from knn_prune                import remove_tiling_overlaps_knn

# Load DNN model
model = params.model_factory()
model.load_weights("./DNN/weights/best_weights.hdf5")

def get_tile_indices(maxvals, overlap = 175):
    all_indx = []
    width = 1024
    height = 1024
    x_max = maxvals[0] # x -> cols
    y_max = maxvals[1] # y -> rows
    num_tiles_x = x_max // (width-overlap)
    overhang_x = x_max % (width-overlap)
    if (not overhang_x==0): num_tiles_x += 1
    num_tiles_y = y_max // (height-overlap)
    overhang_y = y_max % (height-overlap)    
    if (not overhang_y==0): num_tiles_y += 1
    for i in range(num_tiles_x):
        x0 = i*(width - overlap)
        if (i == (num_tiles_x-1)): x0 = i*(width - overlap) - (width - overhang_x)
        all_indx.append([])
        for j in range(num_tiles_y):
            y0 = j*(height - overlap)
            if (j == (num_tiles_y-1)): y0 = j*(width - overlap) - (width - overhang_y)
            all_indx[i].append((x0, y0, width, height))
    return all_indx

def predict_svs_slide(file_name, folder_out, clonal_mark_type, prob_thresh = 0.5):
    start_time = time.time()
    imnumber = file_name.split("/")[-1].split(".")[0]
    try:
        os.mkdir(folder_out)
    except:
        pass
    # Define thresholds for clone finding
    thresh_three, deconv_mat = clone_finding(file_name, clonal_mark_type)
    crypt_contours  = []
    frac_halo       = np.array([])
    frac_halogap    = np.array([]) 
    clone_content   = np.array([])  
    obj_svs  = getROI_svs(file_name, get_roi_plot = False)
    all_indx = get_tile_indices(obj_svs.dims_slides[0], overlap = 175)
    x_tiles = len(all_indx)
    y_tiles = len(all_indx[0])
    for i in range(x_tiles):
        for j in range(y_tiles):            
            # Find next small tile
            xy_vals     = (int(all_indx[i][j][0]), int(all_indx[i][j][1]))
            wh_vals     = (int(all_indx[i][j][2]), int(all_indx[i][j][3]))
            img         = getROI_img_vips(file_name, xy_vals, wh_vals)
            smallBlur_img_nuc, nuclei_ch_raw, clone_ch_raw, backgrd = get_channel_images_for_clone_finding(img, deconv_mat, thresh_three)
            #x_batch.append(img)
            x_batch = [img]
            x_batch = np.array(x_batch, np.float32) / 255.
            
            # Perform prediction and find contours
            predicted_mask_batch = model.predict(x_batch)
            newcnts = mask_to_contours(predicted_mask_batch, prob_thresh)
            newcnts = [cc for cc in newcnts if len(cc)>4] # throw away points and lines (needed in contour class)
            
            ## Add the clone channel features to the list
            clone_features = retrieve_clone_nuclear_features(newcnts, nuclei_ch_raw, clone_ch_raw, backgrd, smallBlur_img_nuc)
            frac_halo       = np.hstack([frac_halo    , clone_features[0]])
            frac_halogap    = np.hstack([frac_halogap , clone_features[1]])
            clone_content   = np.hstack([clone_content, clone_features[2]])
            
            # Add x, y tile offset to all contours (which have been calculated from a tile) for use in full image 
            newcnts = add_offset(newcnts, xy_vals)
            crypt_contours += newcnts
        print("Found %d contours so far, tile %d of %d" % (len(crypt_contours), i*y_tiles+j, x_tiles*y_tiles))
        
    ## Remove tiling overlaps and simplify remaining contours
    print("Of %d contours..." % len(crypt_contours))
    crypt_contours, kept_indices = remove_tiling_overlaps_knn(crypt_contours)
    print("...Keeping only %d due to tiling overlaps." % kept_indices.shape[0])
    
    ## Find clones
    frac_halo       =     frac_halo[kept_indices]
    frac_halogap    =  frac_halogap[kept_indices]
    clone_content   = clone_content[kept_indices]
    clone_channel_feats = (frac_halo , frac_halogap , clone_content)
    clone_contours, full_partial_statistics = find_clones(crypt_cnt, clone_channel_feats, clonal_mark_type, numIQR=2)
    np.savetxt(folder_out + '/.csv', full_partial_statistics, delimiter=",")    
    
    # Join neighbouring clones to make cluster (clone patches that originate via crypt fission)
    # Don't do this if more than 25% of crypts are positive as it's hom tissue
    if len(clone_contours) < 0.25*len(crypt_contours) and len(crypt_contours)>0:
        patch_contours = joinContoursIfClose_OnlyKeepPatches(clone_contours, max_distance = 400)
    else:
        patch_contours = []

    ## Reduce number of vertices per contour to save space/QuPath loading time
    crypt_contours = simplify_contours(crypt_contours)
    clone_contours = simplify_contours(clone_contours)
    patch_contours = simplify_contours(patch_contours)

    write_cnt_text_file(crypt_contours, folder_out + "/crypt_contours.txt")
    write_cnt_text_file(clone_contours, folder_out + "/clone_contours.txt")
    write_cnt_text_file(patch_contours, folder_out + "/patch_contours.txt")
    print("Done " + imnumber + " in " +  str((time.time() - start_time)/60.) + " min =========================================")

def clone_finding(file_name, clonal_mark_type):
    ## Choose deconv mat
    if (clonal_mark_type=="P"): deconv_mat = deconv_mat_KDM6A # Don't have an example of this for a deconvolution matrix        
    if (clonal_mark_type=="N"): deconv_mat = deconv_mat_KDM6A
    if (clonal_mark_type=="PNN"): deconv_mat = deconv_mat_MPAS
    if (clonal_mark_type=="NNN"): deconv_mat = deconv_mat_MAOA
    xyhw = auto_choose_ROI(file_name, deconv_mat, plot_images = False)
    img_for_thresh = getROI_img_vips(file_name, xyhw[0], xyhw[1])
    thresh_cut_nucl, thresh_cut_nucl_blur, th_clone = calculate_thresholds(img_for_thresh, deconv_mat)
    return [thresh_cut_nucl, thresh_cut_nucl_blur, th_clone], deconv_mat

def get_channel_images_for_clone_finding(img, deconv_mat, thresh_three):
    smallBlur_img_nuc, blurred_img_nuc, blurred_img_clone = col_deconvol_and_blur(img, deconv_mat, (11, 11), (37, 37), (27, 27))
    _, nuclei_ch_raw = cv2.threshold( smallBlur_img_nuc, thresh_three[0], 255, cv2.THRESH_BINARY)
    _, nucl_thresh   = cv2.threshold( blurred_img_nuc, thresh_three[1], 255, cv2.THRESH_BINARY)
    _, clone_ch_raw = cv2.threshold(blurred_img_clone, thresh_three[2], 255, cv2.THRESH_BINARY)
    nuclei_ch_raw   = cv2.morphologyEx(nuclei_ch_raw, cv2.MORPH_OPEN,  st_3, iterations=1)
    nucl_thresh_aux = cv2.morphologyEx(  nucl_thresh, cv2.MORPH_OPEN,  st_3, iterations=1)
    foreground = getForeground_mc(nucl_thresh_aux)    
    backgrd    = 255 - foreground
    return smallBlur_img_nuc, nuclei_ch_raw, clone_ch_raw, backgrd
    
def mask_to_contours(preds, thresh):
    contours = []
    for i in range(preds.shape[0]):
        # convert to np.uint8
        pred = (preds[i,:,:,0]*255).astype(np.uint8)
        # perform threshold
        _, mask = cv2.threshold(pred, thresh*255, 255, cv2.THRESH_BINARY)
        # find contours
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2:]
        contours += cnts
    return contours
        
