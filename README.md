# DeCryptICS
# Deep Crypt Image Classifier and Segmenter

The tool is currently a work-in-progress.  Crypt counting is fully functional.

Install instructions in brief:

* Install Miniconda and create a conda environment with Python 3.6
* Run: conda install openslide opencv keras keras-gpu matplotlib scikit-learn numba joblib pandas numpy scipy libiconv ipython 
* Run: pip install xlrd openslide-python
* Run: pip install -U tensorflow-gpu==1.6.0 
* Git clone this repository, https://github.com/MorrisseyLab/DeCryptICS.git
* Download the neural network weights from the Dropbox link listed in install\_instructions.txt and put them in ./DNN/weights/

And that's it! See the install\_instructions.txt file for a step-by-step (Linux) guide that may help, and for details on how to get the nerual network running on your GPU.

Main dependencies are currently:

* OpenSlide
* OpenCV
* TensorFlow / Keras
* ~~PyVips~~

We are aiming to roll these dependencies into a Conda package alongside the DeCryptICS tool. (GPU drivers and CUDA/CUDNN need to be installed separately.)

---

# Preparing input data

Run generate\_block\_list.py and point it at a folder containing the .svs slides to be analysed. If all slides in the folder use the same clonal mark/stain, denote the mark to be used with the flag -c and then the name or number of the mark:

python generate\_block\_list.py /full/path/to/images/ -c KDM6A

or

python generate\_block\_list.py /full/path/to/images/ -c 1

If the folder contains slides with different stainings, for instance if they are different sections of the same block with different treatments, create a file called "slide\_info.csv" with the columns "Image ID" and "mark", denoting the filename (with or without file extension) and the clonal mark used (name or number). Then run generate\_block\_list.py without the -c flag:

python generate\_block\_list.py /full/path/to/block/

This will produce an input\_files.txt listing the paths to the slides and the makr to be used in clone finding.

---

# Running the segmenter

Run run\_script.py with an action and the path to the input file list. For example,

python run\_script.py count /full/path/to/block/input\_files.py

View the help with "python run\_script.py -h" to see all available options.

Note: if running on a GPU and you receive an out-of-memory error, try reducing the "input\_size" in DeCryptICS/DNN/params.py (in nice powers of 2 so that downsampling always creates an integer size: 128, 256, 512, 1024, 2048...)

---

# Analysing the output

Manually curate a list of clones by running manual\_clone\_curation.py and pointing it at the output folder for a given slide (a folder of the form Analysed\_XXXXX). Again, "python manual\_clone\_curation.py -h" will show the help text.

Crypt, mutant crypt, clone and clone patch counts can be found in /path/to/svs/slides/slide\_counts.csv. The clone counts will be updated when manual curation is performed using manual\_clone\_curation.py.

Contours for crypts, clones and patches can be loaded into QuPath using the automatically generated project script load\_contours.groovy within which a variable CLONE\_THRESHOLD can be tweaked to plot clones about which the algorithm was either more certain (CLONE\_THRESHOLD -> 1) or less certain (CLONE\_THRESHOLD -> 0.05). Setting a clone threshold of CLONE\_THRESHOLD = 0 will plot even those clones that were removed during manual curation.
