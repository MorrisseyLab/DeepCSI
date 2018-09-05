#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Mar  6 09:16:23 2018

@author: doran
"""
import tensorflow as tf
import keras.backend as K
import cv2
import numpy as np
import matplotlib.pyplot as plt
import glob
import DNN.u_net as unet
import DNN.params as params
from random           import shuffle
from DNN.augmentation import plot_img, randomHueSaturationValue, randomShiftScaleRotate, randomHorizontalFlip, fix_mask
from DNN.losses       import bce_dice_loss, dice_loss, weighted_bce_dice_loss, weighted_dice_loss
from DNN.losses       import dice_coeff, MASK_VALUE, build_masked_loss, masked_accuracy, masked_dice_coeff
from keras.callbacks  import EarlyStopping, ReduceLROnPlateau, ModelCheckpoint, TensorBoard
from keras.optimizers import RMSprop
from keras.preprocessing.image import img_to_array
from PIL import Image
import io
import keras.callbacks as KC


num_cores = 12
GPU = True
CPU = False

if GPU:
    num_GPU = 1
    num_CPU = 1
if CPU:
    num_CPU = 1
    num_GPU = 0

config = tf.ConfigProto(intra_op_parallelism_threads=num_cores,\
        inter_op_parallelism_threads=num_cores, allow_soft_placement=True,\
        device_count = {'CPU' : num_CPU, 'GPU' : num_GPU})
session = tf.Session(config=config)
K.set_session(session)

input_size = params.input_size
SIZE = (input_size, input_size)
epochs = params.max_epochs
batch_size = params.batch_size

# Processing function for the training data
def train_process(data):
   img_f, mask_f = data
   img = cv2.imread(img_f, cv2.IMREAD_COLOR)
   if (not img.shape==SIZE): img = cv2.resize(img, SIZE)
   
   mask = np.zeros([img.shape[0], img.shape[1], 2]) # for two classifications
   
   # choose which channel to load mask into
   if (mask_f.split('/')[-1].split('.')[-2][-5:]=="crypt"):
      mask[:,:,0] = cv2.imread(mask_f, cv2.IMREAD_GRAYSCALE)
   elif (mask_f.split('/')[-1].split('.')[-2][-4:]=="fufi"):
      mask[:,:,1] = cv2.imread(mask_f, cv2.IMREAD_GRAYSCALE)

   img = randomHueSaturationValue(img,
                                hue_shift_limit=(-100, 100),
                                sat_shift_limit=(0, 0),
                                val_shift_limit=(-25, 25))
   img, mask = randomShiftScaleRotate(img, mask,
                                    shift_limit=(-0.0625, 0.0625),
                                    scale_limit=(-0.1, 0.1),
                                    rotate_limit=(-20, 20))
   img, mask = randomHorizontalFlip(img, mask)
   #fix_mask(mask)
   #mask = np.expand_dims(mask, axis=2)
   
   ## Need to make masking values on outputs in float32 space, as uint8 arrays can't deal with it
   img = img.astype(np.float32) / 255
   mask = mask.astype(np.float32) / 255
   # choose which channel to mask (i.e. all other channels are masked)
   if (mask_f.split('/')[-1].split('.')[-2][-5:]=="crypt"):
      mask[:,:,1].fill(MASK_VALUE)
   elif (mask_f.split('/')[-1].split('.')[-2][-4:]=="fufi"):
      mask[:,:,0].fill(MASK_VALUE)   
   return (img, mask)

def train_generator():
    while True:
        for start in range(0, len(samples), batch_size):
            x_batch = []
            y_batch = []
            end = min(start + batch_size, len(samples))
            ids_train_batch = samples[start:end]
            for ids in ids_train_batch:
                img, mask = train_process(ids)
                x_batch.append(img)
                y_batch.append(mask)
            x_batch = np.array(x_batch)
            y_batch = np.array(y_batch)
            yield x_batch, y_batch

def make_image(tensor):
    """
    Convert an numpy representation image to Image protobuf.
    Copied from https://github.com/lanpa/tensorboard-pytorch/
    """
    height, width, channel = tensor.shape
    image = Image.fromarray(tensor)
    output = io.BytesIO()
    image.save(output, format='PNG')
    image_string = output.getvalue()
    output.close()
    return tf.Summary.Image(height=height,
                            width=width,
                            colorspace=channel,
                            encoded_image_string=image_string)

class TensorBoardImage(KC.Callback):
   def __init__(self, log_dir='./logs', tags=[], test_image_batches=[]):
      super().__init__()
      self.tags = tags
      self.log_dir = log_dir
      self.test_image_batches = test_image_batches

   def on_epoch_end(self, epoch, logs=None):
      writer = tf.summary.FileWriter(self.log_dir)
      for i in range(len(self.tags)):
         batch = self.test_image_batches[i]
         tag = self.tags[i]
         pred = model.predict(batch)         
         image = make_image(batch[0])
         pp = make_image(pred[0,:,:,:])
         
         summary_i = tf.Summary(value=[tf.Summary.Value(tag=tag, image=image)])
         writer.add_summary(summary_i, epoch)
         summary_p = tf.Summary(value=[tf.Summary.Value(tag="pred_"+tag, image=pp)])
         writer.add_summary(summary_p, epoch)
         
      writer.close()
      return

if __name__=="__main__":
   base_folder = "/home/doran/Work/py_code/DeCryptICS/DNN/"
   
   ## Loading old weights into all but the final layer
   #model = params.model_factory(input_shape=(params.input_size, params.input_size, 3))
   #model.load_weights("./DNN/weights/tile256_for_X_best_weights.hdf5")

   # Getting weights layer by layer
   #weights_frozen = [l.get_weights() for l in model.layers]

   # Redefine new network with new classification
   model = params.model_factory(input_shape=(params.input_size, params.input_size, 3), num_classes=2)
   model.load_weights(base_folder+"/weights/cryptandfufi_weights_masking.hdf5")

#   # Add in old weights
#   numlayers = len(model.layers)
#   for i in range(numlayers-1):
#      model.layers[i].set_weights(weights_frozen[i])

#   w_elems = []
#   w_f_elems = weights_frozen[-1]
#   for i in range(len(model.layers[-1].get_weights())):
#      w_elems.append(model.layers[-1].get_weights()[i])   
#   w_elems[0][:,:,:,0] = w_f_elems[0][:,:,:,0]
#   w_elems[1][0] = w_f_elems[1][0]   
#   model.layers[-1].set_weights(w_elems)

   # Freeze all layer but the last classification convolution (as difficult to freeze a subset of parameters within a layer -- but can load them back in afterwards)
#   for layer in model.layers[:-1]:
#      layer.trainable = False
#   # To check whether we have successfully frozen layers, check model.summary() before and after re-compiling
#   model.compile(optimizer=RMSprop(lr=0.0001), loss=build_masked_loss(K.binary_crossentropy), metrics=[masked_dice_coeff])
  
   # Set up training data   
   imgfolder = base_folder + "/input/train/"
   maskfolder = base_folder + "/input/train_masks/"
   images = glob.glob(imgfolder + "*.png")
   samples = []
   #masks = glob.glob(maskfolder + "*.png")
   #for i in range(len(masks)):
      #img = imgfolder+"img"+masks[i][(len(maskfolder)+4):]
      #sample = (img, masks[i])
      #samples.append(sample)
   for i in range(len(images)):
      mask = maskfolder+"mask"+images[i][(len(imgfolder)+3):]
      sample = (images[i], mask)
      samples.append(sample)
   shuffle(samples)
   
   # Define test image batches for TensorBoard checking
   test_img1 = cv2.imread(base_folder+"/input/train/img_674374_4.00-46080-24576-1024-1024_fufi.png")
   test_img2 = cv2.imread(base_folder+"/input/train/img_618446_x6_y1_tile2_1_crypt.png")
   test_img3 = cv2.imread(base_folder+"/input/train/img_618446_x6_y3_tile4_3_crypt.png")
   test_img4 = cv2.imread(base_folder+"/input/train/img_652593_4.00-18432-16384-1024-1024_fufi.png")
   test_img5 = cv2.imread(base_folder+"/input/train/img_601163_x3_y0_tile14_8_crypt.png")
   test_images = [test_img1, test_img2, test_img3, test_img4, test_img5]
   test_batches = []
   for i in range(len(test_images)):
      test_batches.append(np.array([test_images[i]], np.float32) / 255.)
   test_tags = list(np.asarray(range(len(test_batches))).astype(str))
   
   weights_name = base_folder+'/weights/cryptandfufi_weights_masking3.hdf5'
   
   callbacks = [EarlyStopping(monitor='loss', patience=10, verbose=1, min_delta=1e-8),
                ReduceLROnPlateau(monitor='loss', factor=0.1, patience=10, verbose=1, epsilon=1e-8),
                ModelCheckpoint(monitor='loss', filepath=weights_name, save_best_only=True, save_weights_only=True),
                TensorBoard(log_dir=base_folder+'logs'),
                TensorBoardImage(log_dir=base_folder+'logs', tags=test_tags, test_image_batches=test_batches)]
                
   model.fit_generator(generator=train_generator(), steps_per_epoch=np.ceil(float(len(samples)) / float(batch_size)), epochs=epochs, verbose=1, callbacks=callbacks, validation_data=None)
   model.save_weights(weights_name)

