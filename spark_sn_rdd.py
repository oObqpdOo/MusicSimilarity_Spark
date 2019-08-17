#!/usr/bin/python
# -*- coding: utf-8 -*-

import pyspark
import pyspark.ml.feature
import pyspark.mllib.linalg
from scipy.spatial import distance
from pyspark.ml.param.shared import *
from pyspark.mllib.linalg import Vectors, VectorUDT
from pyspark.ml.feature import VectorAssembler
import numpy as np
import scipy as sp
from scipy.signal import butter, lfilter, freqz, correlate2d, sosfilt

from pyspark import SparkContext, SparkConf
from pyspark.sql import SQLContext, Row
from pyspark.sql import SparkSession

import edlib
import sys
import time

confCluster = SparkConf().setAppName("MusicSimilarity Cluster")
confCluster.set("spark.driver.memory", "1g")
confCluster.set("spark.executor.memory", "1g")
confCluster.set("spark.driver.memoryOverhead", "500m")
confCluster.set("spark.executor.memoryOverhead", "500m")
#Be sure that the sum of the driver or executor memory plus the driver or executor memory overhead is always less than the value of yarn.nodemanager.resource.memory-mb
#confCluster.set("yarn.nodemanager.resource.memory-mb", "192000")
#spark.driver/executor.memory + spark.driver/executor.memoryOverhead < yarn.nodemanager.resource.memory-mb
confCluster.set("spark.yarn.executor.memoryOverhead", "512")
#set cores of each executor and the driver -> less than avail -> more executors spawn
confCluster.set("spark.driver.cores", "1")
confCluster.set("spark.executor.cores", "1")
confCluster.set("spark.dynamicAllocation.enabled", "True")
confCluster.set("spark.dynamicAllocation.minExecutors", "4")
confCluster.set("spark.dynamicAllocation.maxExecutors", "4")
confCluster.set("yarn.nodemanager.vmem-check-enabled", "false")
sc = SparkContext(conf=confCluster)
sqlContext = SQLContext(sc)
spark = SparkSession.builder.master("cluster").appName("MusicSimilarity").getOrCreate()

numPartitions = 50
time_dict = {}

total1 = int(round(time.time() * 1000))

def chroma_cross_correlate(chroma1_par, chroma2_par):
    length1 = chroma1_par.size/12
    chroma1 = np.empty([length1,12])
    chroma1 = chroma1_par.reshape(length1, 12)
    length2 = chroma2_par.size/12
    chroma2 = np.empty([length2,12])
    chroma2 = chroma2_par.reshape(length2, 12)
    corr = sp.signal.correlate2d(chroma1, chroma2, mode='full') 
    transposed_chroma = np.transpose(corr)
    mean_line = transposed_chroma[12]
    #print np.max(mean_line)
    return np.max(mean_line)

def chroma_cross_correlate_full(chroma1_par, chroma2_par):
    length1 = chroma1_par.size/12
    chroma1 = np.empty([length1,12])
    length2 = chroma2_par.size/12
    chroma2 = np.empty([length2,12])
    if(length1 > length2):
        chroma1 = chroma1_par.reshape(length1, 12)
        chroma2 = chroma2_par.reshape(length2, 12)
    else:
        chroma2 = chroma1_par.reshape(length1, 12)
        chroma1 = chroma2_par.reshape(length2, 12)    
    corr = sp.signal.correlate2d(chroma1, chroma2, mode='full')
    transposed_chroma = corr.transpose()  
    #print "length1: " + str(length1)
    #print "length2: " + str(length2)
    #transposed_chroma = transposed_chroma / (min(length1, length2))
    index = np.where(transposed_chroma == np.amax(transposed_chroma))
    index = int(index[0])
    #print "index: " + str(index)
    transposed_chroma = transposed_chroma.transpose()
    transposed_chroma = np.transpose(transposed_chroma)
    mean_line = transposed_chroma[index]
    sos = sp.signal.butter(1, 0.1, 'high', analog=False, output='sos')
    mean_line = sp.signal.sosfilt(sos, mean_line)
    #print np.max(mean_line)
    return np.max(mean_line)

def chroma_cross_correlate_valid(chroma1_par, chroma2_par):
    length1 = chroma1_par.size/12
    chroma1 = np.empty([12, length1])
    length2 = chroma2_par.size/12
    chroma2 = np.empty([12, length2])
    if(length1 > length2):
        chroma1 = chroma1_par.reshape(12, length1)
        chroma2 = chroma2_par.reshape(12, length2)
    else:
        chroma2 = chroma1_par.reshape(12, length1)
        chroma1 = chroma2_par.reshape(12, length2)      
    #full
    #correlation = np.zeros([length1 + length2 - 1])
    #valid
    #correlation = np.zeros([max(length1, length2) - min(length1, length2) + 1])
    #same
    correlation = np.zeros([max(length1, length2)])
    for i in range(12):
        correlation = correlation + np.correlate(chroma1[i], chroma2[i], "same")    
    #remove offset to get rid of initial filter peak(highpass of jump from 0-20)
    correlation = correlation - correlation[0]
    sos = butter(1, 0.1, 'high', analog=False, output='sos')
    correlation = sosfilt(sos, correlation)[:]
    return np.max(correlation)

#get 13 mean and 13x13 cov as vectors
def jensen_shannon(vec1, vec2):
    mean1 = np.empty([13, 1])
    mean1 = vec1[0:13]
    #print mean1
    cov1 = np.empty([13,13])
    cov1 = vec1[13:].reshape(13, 13)
    #print cov1
    mean2 = np.empty([13, 1])
    mean2 = vec2[0:13]
    #print mean1
    cov2 = np.empty([13,13])
    cov2 = vec2[13:].reshape(13, 13)
    #print cov1
    mean_m = 0.5 * (mean1 + mean2)
    cov_m = 0.5 * (cov1 + mean1 * np.transpose(mean1)) + 0.5 * (cov2 + mean2 * np.transpose(mean2)) - (mean_m * np.transpose(mean_m))
    div = 0.5 * np.log(np.linalg.det(cov_m)) - 0.25 * np.log(np.linalg.det(cov1)) - 0.25 * np.log(np.linalg.det(cov2))
    #print("JENSEN_SHANNON_DIVERGENCE")    
    if np.isnan(div):
        div = np.inf
        #div = None
    if div <= 0:
        div = div * (-1)
    #print div
    return div

#get 13 mean and 13x13 cov as vectors
def is_invertible(a):
    return a.shape[0] == a.shape[1] and np.linalg.matrix_rank(a) == a.shape[0]

#get 13 mean and 13x13 cov as vectors
def symmetric_kullback_leibler(vec1, vec2):
    mean1 = np.empty([13, 1])
    mean1 = vec1[0:13]
    #print mean1
    cov1 = np.empty([13,13])
    cov1 = vec1[13:].reshape(13, 13)
    #print cov1
    mean2 = np.empty([13, 1])
    mean2 = vec2[0:13]
    #print mean1
    cov2 = np.empty([13,13])
    cov2 = vec2[13:].reshape(13, 13)
    if (is_invertible(cov1) and is_invertible(cov2)):
        d = 13
        div = 0.25 * (np.trace(cov1 * np.linalg.inv(cov2)) + np.trace(cov2 * np.linalg.inv(cov1)) + np.trace( (np.linalg.inv(cov1) + np.linalg.inv(cov2)) * (mean1 - mean2)**2) - 2*d)
    else: 
        div = np.inf
        print("ERROR: NON INVERTIBLE SINGULAR COVARIANCE MATRIX \n\n\n")    
    #print div
    return div

def naive_levenshtein_1(s1, s2):
    if len(s1) < len(s2):
        return naive_levenshtein_1(s2, s1)
    # len(s1) >= len(s2)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1 # j+1 instead of j since previous_row and current_row are one character longer
            deletions = current_row[j] + 1       # than s2
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))

#https://en.wikibooks.org/wiki/Algorithm_Implementation/Strings/Levenshtein_distance#Python
def naive_levenshtein_2(source, target):
    if len(source) < len(target):
        return naive_levenshtein_2(target, source)
    # So now we have len(source) >= len(target).
    if len(target) == 0:
        return len(source)
    # We call tuple() to force strings to be used as sequences
    # ('c', 'a', 't', 's') - numpy uses them as values by default.
    source = np.array(tuple(source))
    target = np.array(tuple(target))
    # We use a dynamic programming algorithm, but with the
    # added optimization that we only need the last two rows
    # of the matrix.
    previous_row = np.arange(target.size + 1)
    for s in source:
        # Insertion (target grows longer than source):
        current_row = previous_row + 1
        # Substitution or matching:
        # Target and source items are aligned, and either
        # are different (cost of 1), or are the same (cost of 0).
        current_row[1:] = np.minimum(
                current_row[1:],
                np.add(previous_row[:-1], target != s))
        # Deletion (target grows shorter than source):
        current_row[1:] = np.minimum(
                current_row[1:],
                current_row[0:-1] + 1)
        previous_row = current_row
    return previous_row[-1]

#even faster than numpy version
def naive_levenshtein(seq1, seq2):
    result = edlib.align(seq1, seq2)
    return(result["editDistance"])


tic1 = int(round(time.time() * 1000))

#########################################################
#   Pre- Process RP for Euclidean
#
rp = sc.textFile("features[0-9]*/out[0-9]*.rp")
rp = rp.map(lambda x: x.replace(' ', '').replace('[', '').replace(']', '').replace(';', ','))
rp = rp.map(lambda x: x.replace('.mp3,', '.mp3;').replace('.wav,', '.wav;').replace('.m4a,', '.m4a;').replace('.aiff,', '.aiff;').replace('.aif,', '.aif;').replace('.au,', '.au;').replace('.flac,', '.flac;').replace('.ogg,', '.ogg;'))
rp = rp.map(lambda x: x.split(';'))
rp = rp.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), x[1].split(",")))
kv_rp= rp.map(lambda x: (x[0], list(x[1:])))
rp_vec = kv_rp.map(lambda x: (x[0], Vectors.dense(x[1]))).persist()
#########################################################
#   Pre- Process RH for Euclidean
#
rh = sc.textFile("features[0-9]*/out[0-9]*.rh")
rh = rh.map(lambda x: x.replace(' ', '').replace('[', '').replace(']', '').replace(';', ','))
rh = rh.map(lambda x: x.replace('.mp3,', '.mp3;').replace('.wav,', '.wav;').replace('.m4a,', '.m4a;').replace('.aiff,', '.aiff;').replace('.aif,', '.aif;').replace('.au,', '.au;').replace('.flac,', '.flac;').replace('.ogg,', '.ogg;'))
rh = rh.map(lambda x: x.split(';'))
rh = rh.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), x[1].split(",")))
kv_rh= rh.map(lambda x: (x[0], list(x[1:])))
rh_vec = kv_rh.map(lambda x: (x[0], Vectors.dense(x[1]))).persist()
#########################################################
#   Pre- Process BH for Euclidean
#
bh = sc.textFile("features[0-9]*/out[0-9]*.bh")
bh = bh.map(lambda x: x.split(";"))
kv_bh = bh.map(lambda x: (x[0].replace(' ', '').replace('[', '').replace(']', '').replace(';', ','), x[1], Vectors.dense(x[2].replace(' ', '').replace('[', '').replace(']', '').split(','))))
bh_vec = kv_bh.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), Vectors.dense(x[2]), x[1])).persist()
#########################################################
#   Pre- Process Notes for Levenshtein
#
notes = sc.textFile("features[0-9]*/out[0-9]*.notes")
notes = notes.map(lambda x: x.split(';'))
notes = notes.map(lambda x: (x[0].replace(' ', '').replace('[', '').replace(']', '').replace(';', ','), x[1], x[2], x[3].replace("10",'K').replace("11",'L').replace("0",'A').replace("1",'B').replace("2",'C').replace("3",'D').replace("4",'E').replace("5",'F').replace("6",'G').replace("7",'H').replace("8",'I').replace("9",'J')))
notes = notes.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), x[3].replace(',','').replace(' ',''), x[1], x[2])).persist()
#########################################################
#   Pre- Process MFCC for Euclidean
#
mfcceuc = sc.textFile("features[0-9]*/out[0-9]*.mfcc")
mfcceuc = mfcceuc.map(lambda x: x.replace(' ', '').replace('[', '').replace(']', '').replace(';', ','))
mfcceuc = mfcceuc.map(lambda x: x.replace('.mp3,', '.mp3;').replace('.wav,', '.wav;').replace('.m4a,', '.m4a;').replace('.aiff,', '.aiff;').replace('.aif,', '.aif;').replace('.au,', '.au;').replace('.flac,', '.flac;').replace('.ogg,', '.ogg;'))
mfcceuc = mfcceuc.map(lambda x: x.split(';'))
mfcceuc = mfcceuc.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), x[1].split(',')))
mfcceucVec = mfcceuc.map(lambda x: (x[0], Vectors.dense(x[1]))).persist()
#########################################################
#   Pre- Process MFCC for SKL and JS
#
mfcc = sc.textFile("features[0-9]*/out[0-9]*.mfcckl")            
mfcc = mfcc.map(lambda x: x.replace(' ', '').replace('[', '').replace(']', '').replace(';', ','))
mfcc = mfcc.map(lambda x: x.replace('.mp3,', '.mp3;').replace('.wav,', '.wav;').replace('.m4a,', '.m4a;').replace('.aiff,', '.aiff;').replace('.aif,', '.aif;').replace('.au,', '.au;').replace('.flac,', '.flac;').replace('.ogg,', '.ogg;'))
mfcc = mfcc.map(lambda x: x.split(';'))
mfcc = mfcc.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), x[1].split(',')))
mfccVec = mfcc.map(lambda x: (x[0], Vectors.dense(x[1]))).persist()
#########################################################
#   Pre- Process Chroma for cross-correlation
#
chroma = sc.textFile("features[0-9]*/out[0-9]*.chroma")
chroma = chroma.map(lambda x: x.replace(' ', '').replace(';', ','))
chroma = chroma.map(lambda x: x.replace('.mp3,', '.mp3;').replace('.wav,', '.wav;').replace('.m4a,', '.m4a;').replace('.aiff,', '.aiff;').replace('.aif,', '.aif;').replace('.au,', '.au;').replace('.flac,', '.flac;').replace('.ogg,', '.ogg;'))
chroma = chroma.map(lambda x: x.split(';'))
#try to filter out empty elements
chroma = chroma.filter(lambda x: (not x[1] == '[]') and (x[1].startswith("[[0.") or x[1].startswith("[[1.")))
chromaRdd = chroma.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ","").replace(' ', '').replace('[', '').replace(']', ''),(x[1].replace(' ', '').replace('[', '').replace(']', '').split(','))))
chromaVec = chromaRdd.map(lambda x: (x[0], Vectors.dense(x[1]))).persist()

#Force Transformation
rp_vec.count()
rh_vec.count()
bh_vec.count()
notes.count()
mfccVec.count()
mfcceucVec.count()
chromaVec.count()
tac1 = int(round(time.time() * 1000))
time_dict['PREPROCESS: ']= tac1 - tic1

def get_neighbors_rp_euclidean(song):
    #########################################################
    #   Get Neighbors
    #  
    comparator = rp_vec.lookup(song.replace(' ', '').replace('[', '').replace(']', '').replace(']', '').replace(';', ','))
    comparator_value = comparator[0]
    resultRP = rp_vec.map(lambda x: (x[0], distance.euclidean(np.array(x[1]), np.array(comparator_value))))
    #OLD AND VERY SLOW WAY    
    #max_val = resultRP.max(lambda x:x[1])[1]
    #min_val = resultRP.min(lambda x:x[1])[1] 
    #WAY BETTER
    stat = resultRP.map(lambda x: x[1]).stats()
    max_val = stat.max()
    min_val = stat.min() 
    resultRP = resultRP.map(lambda x: (x[0], (x[1]-min_val)/(max_val-min_val)))
    return resultRP 

def get_neighbors_rh_euclidean(song):
    #########################################################
    #   Get Neighbors
    #  
    comparator = rh_vec.lookup(song.replace(' ', '').replace('[', '').replace(']', '').replace(';', ','))
    comparator_value = comparator[0]
    resultRH = rh_vec.map(lambda x: (x[0], distance.euclidean(np.array(x[1]), np.array(comparator_value))))
    stat = resultRH.map(lambda x: x[1]).stats()
    max_val = stat.max()
    min_val = stat.min() 
    resultRH = resultRH.map(lambda x: (x[0], (x[1]-min_val)/(max_val-min_val)))
    #resultRH.sortBy(lambda x: x[1]).take(100)    
    return resultRH 

def get_neighbors_bh_euclidean(song):
    #########################################################
    #   Get Neighbors
    #  
    comparator = bh_vec.lookup(song.replace(' ', '').replace('[', '').replace(']', '').replace(';', ','))
    comparator_value = comparator[0]
    #print(np.array(bh_vec.first()[1]))
    #print ( np.array(comparator_value))
    resultBH = bh_vec.map(lambda x: (x[0], distance.euclidean(np.array(x[1]), np.array(comparator_value))))
    stat = resultBH.map(lambda x: x[1]).stats()
    max_val = stat.max()
    min_val = stat.min() 
    resultBH = resultBH.map(lambda x: (x[0], (x[1]-min_val)/(max_val-min_val)))
    #resultBH.sortBy(lambda x: x[1]).take(100)    
    return resultBH 

def get_neighbors_notes(song):
    #########################################################
    #   Get Neighbors
    #  
    comparator = notes.lookup(song.replace(' ', '').replace('[', '').replace(']', '').replace(']', '').replace(';', ','))
    comparator_value = comparator[0]
    resultNotes = notes.map(lambda x: (x[0], naive_levenshtein(str(x[1]), str(comparator_value)), x[1], x[2]))
    stat = resultNotes.map(lambda x: x[1]).stats()
    max_val = stat.max()
    min_val = stat.min() 
    resultNotes = resultNotes.map(lambda x: (x[0], (float(x[1])-min_val)/(max_val-min_val), x[2], x[3]))  
    return resultNotes

def get_neighbors_mfcc_euclidean(song):
    #########################################################
    #   Get Neighbors
    #
    comparator = mfcceucVec.lookup(song.replace(' ', '').replace('[', '').replace(']', '').replace(';', ','))
    comparator_value = Vectors.dense(comparator[0])
    resultMfcc = mfcceucVec.map(lambda x: (x[0], distance.euclidean(np.array(x[1]), np.array(comparator_value)))).cache()
    stat = resultMfcc.map(lambda x: x[1]).stats()
    max_val = stat.max()
    min_val = stat.min() 
    resultMfcc = resultMfcc.map(lambda x: (x[0], (x[1]-min_val)/(max_val-min_val)))
    return resultMfcc

def get_neighbors_mfcc_skl(song):
    #########################################################
    #   Get Neighbors
    #
    comparator = mfccVec.lookup(song.replace(' ', '').replace('[', '').replace(']', '').replace(';', ','))
    comparator_value = Vectors.dense(comparator[0])
    resultMfcc = mfccVec.map(lambda x: (x[0], symmetric_kullback_leibler(np.array(x[1]), np.array(comparator_value))))
    #resultMfcc = resultMfcc.filter(lambda x: x[1] <= 100)      
    resultMfcc = resultMfcc.filter(lambda x: x[1] != np.inf)        
    stat = resultMfcc.map(lambda x: x[1]).stats()
    max_val = stat.max()
    min_val = stat.min()  
    resultMfcc = resultMfcc.map(lambda x: (x[0], (x[1]-min_val)/(max_val-min_val)))
    #resultMfcc.sortBy(lambda x: x[1]).take(100)
    return resultMfcc

def get_neighbors_mfcc_js(song):
    #########################################################
    #   Get Neighbors
    #
    comparator = mfccVec.lookup(song.replace(' ', '').replace('[', '').replace(']', '').replace(';', ','))
    comparator_value = Vectors.dense(comparator[0])
    resultMfcc = mfccVec.map(lambda x: (x[0], jensen_shannon(np.array(x[1]), np.array(comparator_value))))
    #drop non valid rows    
    resultMfcc = resultMfcc.filter(lambda x: x[1] != np.inf)    
    stat = resultMfcc.map(lambda x: x[1]).stats()
    max_val = stat.max()
    min_val = stat.min() 
    resultMfcc = resultMfcc.map(lambda x: (x[0], (x[1]-min_val)/(max_val-min_val)))
    resultMfcc.sortBy(lambda x: x[1]).take(100)
    return resultMfcc

def get_neighbors_chroma_corr_valid(song):
    #########################################################
    #   Get Neighbors
    #
    comparator = chromaVec.lookup(song.replace(' ', '').replace('[', '').replace(']', '').replace(';', ','))
    comparator_value = Vectors.dense(comparator[0])
    #print(np.array(chromaVec.first()[1]))
    #print(np.array(comparator_value))
    resultChroma = chromaVec.map(lambda x: (x[0], chroma_cross_correlate_valid(np.array(x[1]), np.array(comparator_value))))
    #drop non valid rows    
    stat = resultChroma.map(lambda x: x[1]).stats()
    max_val = stat.max()
    min_val = stat.min() 
    resultChroma = resultChroma.map(lambda x: (x[0], (1 - (x[1]-min_val)/(max_val-min_val))))
    resultChroma.sortBy(lambda x: x[1]).take(100)
    return resultChroma

def get_nearest_neighbors_full(song, outname):
    tic1 = int(round(time.time() * 1000))
    neighbors_rp_euclidean = get_neighbors_rp_euclidean(song).persist()
    print(neighbors_rp_euclidean.count())
    tac1 = int(round(time.time() * 1000))
    time_dict['RP: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    neighbors_rh_euclidean = get_neighbors_rh_euclidean(song).persist()   
    print(neighbors_rh_euclidean.count()) 
    tac1 = int(round(time.time() * 1000))
    time_dict['RH: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    neighbors_notes = get_neighbors_notes(song).persist()
    print(neighbors_notes.count())
    tac1 = int(round(time.time() * 1000))
    time_dict['NOTE: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    neighbors_mfcc_eucl = get_neighbors_mfcc_euclidean(song).persist()
    print(neighbors_mfcc_eucl.count())
    tac1 = int(round(time.time() * 1000))
    time_dict['MFCC: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    neighbors_bh_euclidean = get_neighbors_bh_euclidean(song).persist()
    print(neighbors_bh_euclidean.count())
    tac1 = int(round(time.time() * 1000))
    time_dict['BH: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    neighbors_mfcc_skl = get_neighbors_mfcc_skl(song).persist()
    print(neighbors_mfcc_skl.count())
    tac1 = int(round(time.time() * 1000))
    time_dict['SKL: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    neighbors_mfcc_js = get_neighbors_mfcc_js(song).persist()
    print(neighbors_mfcc_js.count())
    tac1 = int(round(time.time() * 1000))
    time_dict['JS: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    neighbors_chroma = get_neighbors_chroma_corr_valid(song).persist()
    print(neighbors_chroma.count())
    tac1 = int(round(time.time() * 1000))
    time_dict['CHROMA: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    mergedSim = neighbors_rp_euclidean.join(neighbors_rh_euclidean)
    mergedSim = mergedSim.map(lambda x: (x[0], list(x[1])))
    mergedSim = mergedSim.join(neighbors_bh_euclidean)
    mergedSim = mergedSim.map(lambda x: (x[0], list(x[1][0]) + [float(x[1][1])]))
    mergedSim = mergedSim.join(neighbors_chroma)
    mergedSim = mergedSim.map(lambda x: (x[0], list(x[1][0]) + [float(x[1][1])]))
    mergedSim = mergedSim.join(neighbors_notes)
    mergedSim = mergedSim.map(lambda x: (x[0], list(x[1][0]) + [float(x[1][1])]))
    mergedSim = mergedSim.join(neighbors_mfcc_eucl)
    mergedSim = mergedSim.map(lambda x: (x[0], list(x[1][0]) + [float(x[1][1])]))
    mergedSim = mergedSim.join(neighbors_mfcc_skl)
    mergedSim = mergedSim.map(lambda x: (x[0], list(x[1][0]) + [float(x[1][1])]))
    mergedSim = mergedSim.join(neighbors_mfcc_js)
    mergedSim = mergedSim.map(lambda x: (x[0], list(x[1][0]) + [float(x[1][1])]))
    mergedSim = mergedSim.map(lambda x: (x[0], x[1], float(np.mean(np.array(x[1]))))).sortBy(lambda x: x[2], ascending = True).persist()
    #print mergedSim.first()
    tac1 = int(round(time.time() * 1000))
    time_dict['JOIN AND AGG: ']= tac1 - tic1
    #mergedSim.map(lambda x: (x[0], float(x[2]))).toDF().toPandas().to_csv(outname, encoding='utf-8')
    mergedSim.unpersist()
    neighbors_rp_euclidean.unpersist()
    neighbors_rh_euclidean.unpersist()    
    neighbors_notes.unpersist()
    neighbors_mfcc_eucl.unpersist()
    neighbors_bh_euclidean.unpersist()
    neighbors_mfcc_skl.unpersist()
    neighbors_mfcc_js.unpersist()
    neighbors_chroma.unpersist()
    return mergedSim

def get_nearest_neighbors_fast(song, outname):
    neighbors_rp_euclidean = get_neighbors_rp_euclidean(song)
    neighbors_notes = get_neighbors_notes(song)
    neighbors_mfcc_eucl = get_neighbors_mfcc_euclidean(song)
    mergedSim = neighbors_mfcc_eucl.join(neighbors_rp_euclidean)
    mergedSim = mergedSim.join(neighbors_notes)
    #mergedSim.toDF().toPandas().to_csv(outname, encoding='utf-8')
    mergedSim = mergedSim.map(lambda x: (x[0], ((x[1][0][1] + x[1][1] + x[1][0][0]) / 3))).sortBy(lambda x: x[1], ascending = True)
    #mergedSim.toDF().toPandas().to_csv(outname, encoding='utf-8')
    return mergedSim

#song = "music/Jazz & Klassik/Keith Jarret - Creation/02-Keith Jarrett-Part II Tokyo.mp3"    #private
#song = "music/Rock & Pop/Sabaton-Primo_Victoria.mp3"           #1517 artists
#song = "music/Electronic/The XX - Intro.mp3"    #100 testset
song = "music/Classical/Katrine_Gislinge-Fr_Elise.mp3"          #1517 artists

if len (sys.argv) < 2:
    #song = "music/Electronic/The XX - Intro.mp3"    #100 testset
    song = "music/Classical/Katrine_Gislinge-Fr_Elise.mp3"
else: 
    song = sys.argv[1]
song = song.replace(";","").replace(".","").replace(",","").replace(" ","")#.encode('utf-8','replace')

#result = get_nearest_neighbors_fast(song, "Electro_rdd_fast.csv")
#result.sortBy(lambda x: x[1], ascending = True).take(10)

result = get_nearest_neighbors_full(song, "RDD FULL.csv")
result.sortBy(lambda x: x[1], ascending = True).take(10)

total2 = int(round(time.time() * 1000))
time_dict['RDD_TOTAL: ']= total2 - total1

#result.map(lambda x: (x[0], float(x[1]))).toDF().toPandas().to_csv(outname, encoding='utf-8')
result.map(lambda x: (x[0], float(x[2]))).toDF().toPandas().to_csv("RDD_FULL.csv", encoding='utf-8')

print time_dict

rp_vec.unpersist()
rh_vec.unpersist()
bh_vec.unpersist()
notes.unpersist()
mfccVec.unpersist()
mfcceucVec.unpersist()
chromaVec.unpersist()
