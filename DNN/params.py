from DNN.u_net import get_unet_256_for_X

# Reduce input_size if GPU is running out of memory (increase if not!)
input_size = 2048 #1024

max_epochs = 10000
batch_size = 16

orig_width = input_size
orig_height = input_size

threshold = 0.5

model_factory = get_unet_256_for_X
