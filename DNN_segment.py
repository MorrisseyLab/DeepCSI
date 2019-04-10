#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Mar 26 15:47:40 2018

@author: doran
"""
import cv2, os, time
import keras
import pickle
import tensorflow as tf
import numpy      as np
import DNN.u_net  as unet
import DNN.params as params
from keras                     import backend as K
from keras.preprocessing.image import img_to_array
from knn_prune                 import *
from MiscFunctions             import simplify_contours, write_clone_image_snips,\
                                     convert_to_local_clone_indices, mkdir_p,\
                                     getROI_img_osl, add_offset, write_cnt_text_file,\
                                     rescale_contours, write_score_text_file
from cnt_Feature_Functions     import joinContoursIfClose_OnlyKeepPatches, contour_Area,\
                                     contour_EccMajorMinorAxis, contour_xy
from GUI_ChooseROI_class       import getROI_svs

#model = params.model_factory(input_shape=(params.input_size, params.input_size, 3), num_classes=5)
#maindir = os.path.dirname(os.path.abspath(__file__))
#weightsin = os.path.join(maindir, 'DNN', 'weights', 'cryptfuficlone_weights.hdf5')
#model.load_weights(weightsin)
#model.load_weights("./DNN/weights/cryptfuficlone_weights.hdf5")

def get_tile_indices(maxvals, overlap = 50, SIZE = (params.input_size, params.input_size)):
    all_indx = []
    width = SIZE[0]
    height = SIZE[1]
    x_max = maxvals[0] # x -> cols
    y_max = maxvals[1] # y -> rows
    num_tiles_x = x_max // (width-overlap)
    endpoint_x  = num_tiles_x*(width-overlap) + overlap    
    overhang_x  = x_max - endpoint_x
    if (overhang_x>0): num_tiles_x += 1
    
    num_tiles_y = y_max // (height-overlap)
    endpoint_y  = num_tiles_y*(height-overlap) + overlap    
    overhang_y  = y_max - endpoint_y
    if (overhang_y>0): num_tiles_y += 1   
     
    for i in range(num_tiles_x):
        x0 = i*(width - overlap)
        if (i == (num_tiles_x-1)): x0 = x_max - width
        all_indx.append([])
        for j in range(num_tiles_y):
            y0 = j*(height - overlap)
            if (j == (num_tiles_y-1)): y0 = y_max - height
            all_indx[i].append((x0, y0, width, height))
    return all_indx

def predict_svs_slide(file_name, folder_to_analyse, clonal_mark_type, model, prob_thresh = 0.5, write_clone_imgs = False):
   start_time = time.time()
   imnumber = file_name.split("/")[-1].split(".")[0]
   mkdir_p(folder_to_analyse)
   crypt_contours  = []
   fufi_contours  = []
   clone_contours = []
   K = clonal_mark_type+1 # moving from (1,2,3) to (2,3,4) for correct slice indexing
       
   ## Tiling
   obj_svs  = getROI_svs(file_name, get_roi_plot = False)
   size = (params.input_size, params.input_size)
   scaling_val = obj_svs.dims_slides[0][0] / float(obj_svs.dims_slides[1][0])
   all_indx = get_tile_indices(obj_svs.dims_slides[1], overlap = 50, SIZE = size)
   x_tiles = len(all_indx)
   y_tiles = len(all_indx[0])   
   
   for i in range(x_tiles):
      for j in range(y_tiles):
         xy_vals = (int(all_indx[i][j][0]), int(all_indx[i][j][1]))
         wh_vals = (int(all_indx[i][j][2]), int(all_indx[i][j][3]))
         img = getROI_img_osl(file_name, xy_vals, wh_vals, level = 1)
         x_batch = [img]
         x_batch = np.array(x_batch, np.float32) / 255.

         # Perform prediction and find contours
         predicted_mask_batch = model.predict(x_batch)
         newcnts = mask_to_contours(predicted_mask_batch, prob_thresh)
         for k in range(predicted_mask_batch.shape[3]):
            newcnts[k] = [cc for cc in newcnts[k] if len(cc)>4] # throw away points and lines (needed in contour class)
         
         # Add x, y tile offset to all contours (which have been calculated from a tile) for use in full (scaled) image 
         for k in range(predicted_mask_batch.shape[3]):
            newcnts[k] = add_offset(newcnts[k], xy_vals)

         # Add to lists
         crypt_contours += newcnts[0]
         fufi_contours  += [cc for cc in newcnts[1] if contour_Area(cc)>(800./(scaling_val*scaling_val))]         
         clone_contours += newcnts[K]
      print("Found %d crypt contours, %d fufi contours and %d clone contours so far, tile %d of %d" % (len(crypt_contours), len(fufi_contours), len(clone_contours), i*y_tiles+j + 1, x_tiles*y_tiles))         
   del img, predicted_mask_batch, newcnts
   
   ## Remove tiling overlaps and simplify remaining contours
   print("Of %d crypt contours, %d fufi contours and %d clone contours..." % (len(crypt_contours), len(fufi_contours), len(clone_contours)))
   oldlen = 1
   newlen = 0
   while newlen!=oldlen:
      oldlen = len(crypt_contours)
      crypt_contours, ki_cry = remove_tiling_overlaps_knn(crypt_contours)
      fufi_contours , ki_fuf = remove_tiling_overlaps_knn(fufi_contours)
      clone_contours, ki_clo = remove_tiling_overlaps_knn(clone_contours)
      newlen = len(crypt_contours)
   print("...Keeping only %d, %d and %d due to tiling overlaps." % (len(crypt_contours), len(fufi_contours), len(clone_contours)))
   
   ## Sanity check for zero crypts
   if (newlen==0):
      print("Outputting zilch as no crypts found!")
      
   else:
      print("Processing clones and fufis...")
      ## Reduce number of vertices per contour to save space/QuPath loading time
      crypt_contours = simplify_contours(crypt_contours)
      fufi_contours  = simplify_contours(fufi_contours)
      clone_contours = simplify_contours(clone_contours)

      ## Convert contours to fullscale image coordinates
      crypt_contours = rescale_contours(crypt_contours, scaling_val)
      fufi_contours  = rescale_contours(fufi_contours, scaling_val)
      clone_contours = rescale_contours(clone_contours, scaling_val)

      ## Assess overlap of crypt contours with fufi and clone contours,
      ## thus build up an index system for the crypt contours to assess a knn network
      fixed_crypt_contours, fixed_fufi_contours, fixed_clone_contours, crypt_dict = fix_fufi_clone_patch_specifications(crypt_contours, fufi_contours, clone_contours)
         
      ## Fix fufi clones and join patches
      clone_scores, patch_contours, patch_sizes, patch_indices, patch_indices_local, crypt_dict = fix_patch_specification(fixed_crypt_contours, fixed_clone_contours, crypt_dict)

      ## Find each crypt's patch size (and possibly their patch neighbours' ID/(x,y)?)
      crypt_dict = get_crypt_patchsizes_and_ids(patch_indices, crypt_dict)
      
      ## Add crypt features
      crypt_dict = get_contour_features(fixed_crypt_contours, crypt_dict)
      print("...Done!")
      
      ## Save output
      print("Saving results...")
      write_cnt_text_file(fixed_crypt_contours, folder_to_analyse + "/crypt_contours.txt")
      write_cnt_text_file(fixed_fufi_contours , folder_to_analyse + "/fufi_contours.txt")
      write_cnt_text_file(fixed_clone_contours, folder_to_analyse + "/clone_contours.txt")
      write_cnt_text_file(patch_contours      , folder_to_analyse + "/patch_contours.txt")
      write_score_text_file(patch_sizes       , folder_to_analyse + "/patch_sizes.txt")
      write_score_text_file(clone_scores      , folder_to_analyse + "/clone_scores.txt")
      pickle.dump( patch_indices_local ,  open( folder_to_analyse + "/patch_indices.pickle", "wb" ) )
      if write_clone_imgs==True: write_clone_image_snips(folder_to_analyse, file_name, fixed_clone_contours[:25], scaling_val)
      with open(folder_to_analyse + "/crypt_network_data.txt", 'w') as fo:
         fo.write("#<x>\t<y>\t<fufi>\t<mutant>\t<patch_size>\t<patch_id>\t<area>\t<eccentricity>\t<major_axis>\t<minor_axis>\n")
         for i in range(len(fixed_crypt_contours)):
            fo.write("%d\t%d\t%d\t%d\t%d\t%d\t%1.8g\t%1.8g\t%1.8g\t%1.8g\n" % (crypt_dict["crypt_xy"][i,0], crypt_dict["crypt_xy"][i,1], crypt_dict["fufi_label"][i], 
                                                                               crypt_dict["clone_label"][i], crypt_dict["patch_size"][i], crypt_dict["patch_id"][i],
                                                                               crypt_dict["area"][i], crypt_dict["ecc"][i], crypt_dict["majorax"][i], crypt_dict["minorax"][i]))
   print("...Done " + imnumber + " in " +  str((time.time() - start_time)/60.) + " min =========================================")
   
def predict_image(file_name, folder_to_analyse, clonal_mark_type, model, prob_thresh = 0.75, downsample = True, write_clone_imgs = False):
   start_time = time.time()
   imnumber = file_name.split("/")[-1].split(".")[0]
   mkdir_p(folder_to_analyse)
   crypt_contours  = []
   fufi_contours  = []
   clone_contours = []
   K = clonal_mark_type+1 # moving from (1,2,3) to (2,3,4) for correct slice indexing
       
   ## Tiling
   img_full  = cv2.imread(file_name)
   size = (params.input_size, params.input_size)
   if (downsample):
      img_full = cv2.pyrDown(img_full)
      img_full = cv2.pyrDown(img_full)
   if (img_full.shape[0]<params.input_size or img_full.shape[1]<params.input_size):
      rownum = np.maximum(img_full.shape[0], int(1.5*params.input_size))
      colnum = np.maximum(img_full.shape[1], int(1.5*params.input_size))
      img_full_c = np.ones((rownum, colnum, img_full.shape[2]), dtype=np.uint8) * 255
      img_full_c[:img_full.shape[0], :img_full.shape[1]] = img_full
      img_full = img_full_c
   scaling_val = 4.
   all_indx = get_tile_indices(img_full.shape, overlap = 50, SIZE = size)
   x_tiles = len(all_indx)
   y_tiles = len(all_indx[0])   
   
   for i in range(x_tiles):
      for j in range(y_tiles):
         xy_vals = (int(all_indx[i][j][0]), int(all_indx[i][j][1]))
         wh_vals = (int(all_indx[i][j][2]), int(all_indx[i][j][3]))
         img = img_full[xy_vals[0]:(xy_vals[0]+wh_vals[0]), xy_vals[1]:(xy_vals[1]+wh_vals[1])]
         x_batch = [img]
         x_batch = np.array(x_batch, np.float32) / 255.

         # Perform prediction and find contours
         predicted_mask_batch = model.predict(x_batch)
         newcnts = mask_to_contours(predicted_mask_batch, prob_thresh)
         for k in range(predicted_mask_batch.shape[3]):
            newcnts[k] = [cc for cc in newcnts[k] if len(cc)>4] # throw away points and lines (needed in contour class)
         
         # Add x, y tile offset to all contours (which have been calculated from a tile) for use in full (scaled) image 
         for k in range(predicted_mask_batch.shape[3]):
            newcnts[k] = add_offset(newcnts[k], xy_vals)

         # Add to lists
         crypt_contours += newcnts[0]
         fufi_contours  += [cc for cc in newcnts[1] if contour_Area(cc)>(400./(scaling_val*scaling_val))]         
         clone_contours += newcnts[K]
      print("Found %d crypt contours, %d fufi contours and %d clone contours so far, tile %d of %d" % (len(crypt_contours), len(fufi_contours), len(clone_contours), i*y_tiles+j + 1, x_tiles*y_tiles))         
   del img, predicted_mask_batch, newcnts
   
   ## Remove tiling overlaps and simplify remaining contours
   print("Of %d crypt contours, %d fufi contours and %d clone contours..." % (len(crypt_contours), len(fufi_contours), len(clone_contours)))
   oldlen = 1
   newlen = 0
   while newlen!=oldlen:
      oldlen = len(crypt_contours)
      crypt_contours, ki_cry = remove_tiling_overlaps_knn(crypt_contours)
      fufi_contours , ki_fuf = remove_tiling_overlaps_knn(fufi_contours)
      clone_contours, ki_clo = remove_tiling_overlaps_knn(clone_contours)
      newlen = len(crypt_contours)
   print("...Keeping only %d, %d and %d due to tiling overlaps." % (len(crypt_contours), len(fufi_contours), len(clone_contours)))
   
   ## Sanity check for zero crypts
   if (newlen==0):
      print("Outputting zilch as no crypts found!")
      
   else:
      ## Reduce number of vertices per contour to save space/QuPath loading time
      crypt_contours = simplify_contours(crypt_contours)
      fufi_contours  = simplify_contours(fufi_contours)
      clone_contours = simplify_contours(clone_contours)

      ## Convert contours to fullscale image coordinates
      crypt_contours = rescale_contours(crypt_contours, scaling_val)
      fufi_contours  = rescale_contours(fufi_contours, scaling_val)
      clone_contours = rescale_contours(clone_contours, scaling_val)

      ## Assess overlap of crypt contours with fufi and clone contours,
      ## thus build up an index system for the crypt contours to assess a knn network
      fixed_crypt_contours, fixed_fufi_contours, fixed_clone_contours, crypt_dict = fix_fufi_clone_patch_specifications(crypt_contours, fufi_contours, clone_contours)
         
      ## Fix fufi clones and join patches
      clone_scores, patch_contours, patch_sizes, patch_indices, patch_indices_local, crypt_dict = fix_patch_specification(fixed_crypt_contours, fixed_clone_contours, crypt_dict)

      ## Find each crypt's patch size (and possibly their patch neighbours' ID/(x,y)?)
      ## TO DO: and give each crypt a patch-specific ID (so we know what patch it belongs to)
      crypt_dict = get_crypt_patchsizes_and_ids(patch_indices, crypt_dict)

      ## Add crypt features
      crypt_dict = get_contour_features(fixed_crypt_contours, crypt_dict)
      
      ## Save output
      write_cnt_text_file(fixed_crypt_contours, folder_to_analyse + "/crypt_contours.txt")
      write_cnt_text_file(fixed_fufi_contours , folder_to_analyse + "/fufi_contours.txt")
      write_cnt_text_file(fixed_clone_contours, folder_to_analyse + "/clone_contours.txt")
      write_cnt_text_file(patch_contours      , folder_to_analyse + "/patch_contours.txt")
      write_score_text_file(patch_sizes       , folder_to_analyse + "/patch_sizes.txt")
      write_score_text_file(clone_scores      , folder_to_analyse + "/clone_scores.txt")
      pickle.dump( patch_indices_local ,  open( folder_to_analyse + "/patch_indices.pickle", "wb" ) )
      if write_clone_imgs==True: write_clone_image_snips(folder_to_analyse, file_name, fixed_clone_contours, scaling_val)
      with open(folder_to_analyse + "/crypt_network_data.txt", 'w') as fo:
         fo.write("#<x>\t<y>\t<fufi>\t<mutant>\t<patch_size>\t<patch_id>\t<area>\t<eccentricity>\t<major_axis>\t<minor_axis>\n")
         for i in range(len(fixed_crypt_contours)):
            fo.write("%d\t%d\t%d\t%d\t%d\t%d\t%1.8g\t%1.8g\t%1.8g\t%1.8g\n" % (crypt_dict["crypt_xy"][i,0], crypt_dict["crypt_xy"][i,1], crypt_dict["fufi_label"][i], 
                                                                               crypt_dict["clone_label"][i], crypt_dict["patch_size"][i], crypt_dict["patch_id"][i],
                                                                               crypt_dict["area"][i], crypt_dict["ecc"][i], crypt_dict["majorax"][i], crypt_dict["minorax"][i]))
   print("Done " + imnumber + " in " +  str((time.time() - start_time)/60.) + " min =========================================")      
  
def mask_to_contours(preds, thresh):
   n_class = preds.shape[3]
   all_class_cnts = []
   for j in range(n_class): # ensure correct number of elements
      all_class_cnts.append([])
   for j in range(n_class):
      contours = []
      for i in range(preds.shape[0]):
         # convert to np.uint8
         pred = (preds[i,:,:,j]*255).astype(np.uint8)
         # perform threshold
         _, mask = cv2.threshold(pred, thresh*255, 255, cv2.THRESH_BINARY)
         # find contours
         cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2:]
         contours += cnts
      all_class_cnts[j] += contours
   return all_class_cnts

def fix_fufi_clone_patch_specifications(crypt_contours, fufi_contours, clone_contours):
   ## Assess overlap of crypt contours with fufi and clone contours,
   ## thus build up an index system for the crypt contours to assess a knn network
   fixed_crypt_contours = check_length(crypt_contours)
   fixed_fufi_contours  = check_length(fufi_contours)
   fixed_clone_contours = check_length(clone_contours)
   crypt_dict = {}
   crypt_dict["crypt_xy"]    = np.array([contour_xy(cnt_i) for cnt_i in fixed_crypt_contours])
   crypt_dict["fufi_label"]  = np.zeros(len(fixed_crypt_contours))
   crypt_dict["clone_label"] = np.zeros(len(fixed_crypt_contours))
   crypt_dict["patch_size"]  = np.zeros(len(fixed_crypt_contours))
   # Join crypts inside same fufi; cull fufis not containing crypts; extend fufis that contain second nearest but not nearest crypt
   oldlen = 1; oldlen1 = 1
   newlen = 0; newlen1 = 0
   if (len(fixed_fufi_contours)>0):
      while (newlen!=oldlen or newlen1!=oldlen1):
         oldlen  = len(fixed_crypt_contours)
         oldlen1 = len(fixed_fufi_contours)
         fixed_crypt_contours, fixed_fufi_contours, crypt_dict = crypt_indexing_fufi(fixed_crypt_contours, fixed_fufi_contours, nn=4, crypt_dict=crypt_dict)
         newlen  = len(fixed_crypt_contours)
         newlen1 = len(fixed_fufi_contours)

   # Join clones inside the same fufi
   if (len(fixed_fufi_contours)>0 and len(fixed_clone_contours)>0):
      fixed_clone_contours = join_clones_in_fufi(fixed_clone_contours, fixed_fufi_contours, nn=4)
   return fixed_crypt_contours, fixed_fufi_contours, fixed_clone_contours, crypt_dict
   
def fix_patch_specification(fixed_crypt_contours, fixed_clone_contours, crypt_dict):
   patch_contours, patch_sizes, patch_indices, patch_indices_local = [], [], [], []
   clone_scores = np.array([])
   if (len(fixed_clone_contours)>0):
      # Label crypts as clones
      crypt_dict = crypt_indexing_clone(fixed_crypt_contours, fixed_clone_contours, nn=1, crypt_dict=crypt_dict)
      clone_inds = np.where(crypt_dict["clone_label"]==1)[0]
      clone_scores = np.ones(len(fixed_clone_contours))*0.5 # default score is 1/2 before manual curation
      # Join patches as contours
      if (len(fixed_clone_contours) < 0.25*len(fixed_crypt_contours) and len(fixed_crypt_contours)>0 and len(fixed_clone_contours)>1):
         patch_contours, patch_sizes, patch_indices = joinContoursIfClose_OnlyKeepPatches(fixed_crypt_contours, crypt_dict, clone_inds)
         patch_indices_local = convert_to_local_clone_indices(patch_indices, clone_inds)  
   return clone_scores, patch_contours, patch_sizes, patch_indices, patch_indices_local, crypt_dict

def get_contour_features(fixed_crypt_contours, crypt_dict):
   crypt_dict["area"] = np.asarray([contour_Area(i) for i in fixed_crypt_contours])
   eccmajorminor = [contour_EccMajorMinorAxis(i) for i in fixed_crypt_contours]
   crypt_dict["ecc"] = np.zeros(crypt_dict["area"].shape[0])
   crypt_dict["majorax"] = np.zeros(crypt_dict["area"].shape[0])
   crypt_dict["minorax"] = np.zeros(crypt_dict["area"].shape[0])
   for i in range(len(eccmajorminor)):
      crypt_dict["ecc"][i] = eccmajorminor[i][0]
      crypt_dict["majorax"][i] = eccmajorminor[i][1]
      crypt_dict["minorax"][i] = eccmajorminor[i][2]
   return crypt_dict

