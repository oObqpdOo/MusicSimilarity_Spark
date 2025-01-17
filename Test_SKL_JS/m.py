#!/usr/bin/python
# -*- coding: utf-8 -*-

import pyspark
import pyspark.ml.feature
import pyspark.mllib.linalg
import pyspark.ml.param
import pyspark.sql.functions
from pyspark.sql import functions as F
from pyspark.sql.types import *
from pyspark.sql.functions import udf
from scipy.spatial import distance
#only version 2.1 upwards
#from pyspark.ml.feature import BucketedRandomProjectionLSH
from pyspark.mllib.linalg import Vectors
from pyspark.ml.param.shared import *
from pyspark.mllib.linalg import Vectors, VectorUDT
from pyspark.ml.feature import VectorAssembler
import numpy as np
#import org.apache.spark.sql.functions.typedLit
from pyspark.sql.functions import lit
from pyspark.sql.functions import levenshtein  
from pyspark.sql.functions import col
from pyspark.sql.functions import desc
from pyspark.sql.functions import asc
import scipy as sp
from scipy.signal import butter, lfilter, freqz, correlate2d, sosfilt
import time
from pyspark import SparkContext, SparkConf
from pyspark.sql import SQLContext, Row
import sys
import edlib

total1 = int(round(time.time() * 1000))

confCluster = SparkConf().setAppName("MusicSimilarity Cluster")
confCluster.set("spark.driver.memory", "64g")
confCluster.set("spark.executor.memory", "64g")
confCluster.set("spark.driver.memoryOverhead", "32g")
confCluster.set("spark.executor.memoryOverhead", "32g")
#Be sure that the sum of the driver or executor memory plus the driver or executor memory overhead is always less than the value of yarn.nodemanager.resource.memory-mb
#confCluster.set("yarn.nodemanager.resource.memory-mb", "196608")
#spark.driver/executor.memory + spark.driver/executor.memoryOverhead < yarn.nodemanager.resource.memory-mb
confCluster.set("spark.yarn.executor.memoryOverhead", "4096")
#set cores of each executor and the driver -> less than avail -> more executors spawn
confCluster.set("spark.driver.cores", "36")
confCluster.set("spark.executor.cores", "36")
#confCluster.set("spark.shuffle.service.enabled", "True")
confCluster.set("spark.dynamicAllocation.enabled", "True")
#confCluster.set("spark.dynamicAllocation.initialExecutors", "16")
#confCluster.set("spark.dynamicAllocation.executorIdleTimeout", "30s")	
confCluster.set("spark.dynamicAllocation.minExecutors", "15")
confCluster.set("spark.dynamicAllocation.maxExecutors", "15")
confCluster.set("yarn.nodemanager.vmem-check-enabled", "false")
repartition_count = 32


sc = SparkContext(conf=confCluster)
sqlContext = SQLContext(sc)
time_dict = {}

sc.setLogLevel("ERROR")

debug_dict = {}
negjs = sc.accumulator(0)
nanjs = sc.accumulator(0)
nonpdjs = sc.accumulator(0)
negskl = sc.accumulator(0)
nanskl = sc.accumulator(0)
noninskl = sc.accumulator(0)

def jensen_shannon(vec1, vec2):
    d = 13
    mean1 = np.empty([d, 1])
    mean1 = vec1[0:d]
    cov1 = np.empty([d,13])
    cov1 = vec1[d:].reshape(d, d)
    div = np.inf
    #div = float('NaN')
    try:
        cov_1_logdet = 2*np.sum(np.log(np.linalg.cholesky(cov1).diagonal()))
        issing1=1
    except np.linalg.LinAlgError as err:
        nonpdjs.add(1)
        #print("ERROR: NON POSITIVE DEFINITE MATRIX 1\n\n\n") 
        return div    
    #print(cov_1_logdet)
    mean2 = np.empty([d, 1])
    mean2 = vec2[0:d]
    cov2 = np.empty([d,d])
    cov2 = vec2[d:].reshape(d, d)
    try:
        cov_2_logdet = 2*np.sum(np.log(np.linalg.cholesky(cov2).diagonal()))
        issing2=1
    except np.linalg.LinAlgError as err:
        nonpdjs.add(1)
        #print("ERROR: NON POSITIVE DEFINITE MATRIX 2\n\n\n") 
        return div
    #print(cov_2_logdet)
    #==============================================
    if (issing1==1) and (issing2==1):
        mean_m = 0.5 * mean1 +  0.5 * mean2
        cov_m = 0.5 * (cov1 + np.outer(mean1, mean1)) + 0.5 * (cov2 + np.outer(mean2, mean2)) - np.outer(mean_m, mean_m)
        cov_m_logdet = 2*np.sum(np.log(np.linalg.cholesky(cov_m).diagonal()))
        #print(cov_m_logdet)
        try:        
            div = 0.5 * cov_m_logdet - 0.25 * cov_1_logdet - 0.25 * cov_2_logdet
        except np.linalg.LinAlgError as err:
            nonpdjs.add(1)
            #print("ERROR: NON POSITIVE DEFINITE MATRIX M\n\n\n") 
            return div
        #print("JENSEN_SHANNON_DIVERGENCE")   
    if np.isnan(div):
        div = np.inf
        nanjs.add(1)
        #div = None
        pass
    if div <= 0:
        div = 0
        negjs.add(1)
        pass
    #print(div)
    return div

#get 13 mean and 13x13 cov as vectors
def symmetric_kullback_leibler(vec1, vec2):
    d = 13
    mean1 = np.empty([d, 1])
    mean1 = vec1[0:d]
    cov1 = np.empty([d,d])
    cov1 = vec1[d:].reshape(d, d)
    mean2 = np.empty([d, 1])
    mean2 = vec2[0:d]
    cov2 = np.empty([d,d])
    cov2 = vec2[d:].reshape(d, d)
    div = np.inf
    try:
        g_chol = np.linalg.cholesky(cov1)
        g_ui   = np.linalg.solve(g_chol,np.eye(d))
        icov1  = np.matmul(np.transpose(g_ui), g_ui)
        isinv1=1
    except np.linalg.LinAlgError as err:
        isinv1=0
    try:
        g_chol = np.linalg.cholesky(cov2)
        g_ui   = np.linalg.solve(g_chol,np.eye(d))
        icov2  = np.matmul(np.transpose(g_ui), g_ui)
        isinv2=1
    except np.linalg.LinAlgError as err:
        isinv2=0
    #================================
    if (isinv1==1) and (isinv2==1):
        temp_a = np.trace(np.matmul(cov1, icov2)) 
        #temp_a = traceprod(cov1, icov2) 
        #print(temp_a)
        temp_b = np.trace(np.matmul(cov2, icov1))
        #temp_b = traceprod(cov2, icov1)
        #print(temp_b)
        temp_c = np.trace(np.matmul((icov1 + icov2), np.outer((mean1 - mean2), (mean1 - mean2))))
        #print(temp_c)        
        div = 0.25 * (temp_a + temp_b + temp_c - 2*d)
    else: 
        div = np.inf
        noninskl.add(1)
        #print("ERROR: NON INVERTIBLE SINGULAR COVARIANCE MATRIX \n\n\n")    
    if div <= 0:
        #print("Temp_a: " + temp_a + "\n Temp_b: " + temp_b + "\n Temp_c: " + temp_c)
        div = 0
        negskl.add(1)
    if np.isnan(div):
        div = np.inf
        nanskl.add(1)
        #div = None
    #print(div)
    return div

#get 13 mean and 13x13 cov + var as vectors
def get_euclidean_mfcc(vec1, vec2):
    mean1 = np.empty([13, 1])
    mean1 = vec1[0:13]
    cov1 = np.empty([13,13])
    cov1 = vec1[13:].reshape(13, 13)        
    mean2 = np.empty([13, 1])
    mean2 = vec2[0:13]
    cov2 = np.empty([13,13])
    cov2 = vec2[13:].reshape(13, 13)
    iu1 = np.triu_indices(13)
    #You need to pass the arrays as an iterable (a tuple or list), thus the correct syntax is np.concatenate((,),axis=None)
    div = distance.euclidean(np.concatenate((mean1, cov1[iu1]),axis=None), np.concatenate((mean2, cov2[iu1]),axis=None))
    return div


#get 13 mean and 13x13 cov + var as vectors
def get_euclidean_mfcc(vec1, vec2):
    mean1 = np.empty([13, 1])
    mean1 = vec1[0:13]
    cov1 = np.empty([13,13])
    cov1 = vec1[13:].reshape(13, 13)        
    mean2 = np.empty([13, 1])
    mean2 = vec2[0:13]
    cov2 = np.empty([13,13])
    cov2 = vec2[13:].reshape(13, 13)
    iu1 = np.triu_indices(13)
    #You need to pass the arrays as an iterable (a tuple or list), thus the correct syntax is np.concatenate((,),axis=None)
    div = distance.euclidean(np.concatenate((mean1, cov1[iu1]),axis=None), np.concatenate((mean2, cov2[iu1]),axis=None))
    return div

tic1 = int(round(time.time() * 1000))
list_to_vector_udf = udf(lambda l: Vectors.dense(l), VectorUDT())



#########################################################
#   Pre- Process MFCC for SKL and JS and EUC
#

mfcc = sc.textFile("features[0-9]*/out[0-9]*.mfcckl")            
mfcc = mfcc.map(lambda x: x.replace(' ', '').replace(';', ','))
mfcc = mfcc.map(lambda x: x.replace('.mp3,', '.mp3;').replace('.wav,', '.wav;').replace('.m4a,', '.m4a;').replace('.aiff,', '.aiff;').replace('.aif,', '.aif;').replace('.au,', '.au;').replace('.flac,', '.flac;').replace('.ogg,', '.ogg;'))
mfcc = mfcc.map(lambda x: x.split(';'))
mfcc = mfcc.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), x[1].replace('[', '').replace(']', '').split(',')))
mfccVec = mfcc.map(lambda x: (x[0], Vectors.dense(x[1])))
mfccDfMerged = sqlContext.createDataFrame(mfccVec, ["id", "mfccSkl"])

#########################################################
#   Gather all features in one dataframe
#
featureDF = mfccDfMerged.dropDuplicates().persist()

#Force lazy evaluation to evaluate with an action
trans = featureDF.count()
#print(featureDF.count())

#########################################################
#  16 Nodes, 192GB RAM each, 36 cores each (+ hyperthreading = 72)
#   -> max 1152 executors

fullFeatureDF = featureDF.repartition(repartition_count).persist()
#print(fullFeatureDF.count())
#fullFeatureDF.toPandas().to_csv("featureDF.csv", encoding='utf-8')
tac1 = int(round(time.time() * 1000))
time_dict['PREPROCESS: ']= tac1 - tic1

def get_neighbors_mfcc_skl(song, featureDF):
    comparator_value = song[0]["mfccSkl"]
    distance_udf = F.udf(lambda x: float(symmetric_kullback_leibler(x, comparator_value)), DoubleType())
    result = featureDF.withColumn('distances_skl', distance_udf(F.col('mfccSkl'))).select("id", "distances_skl")
    #result = featureDF.withColumn("compare", lit(str(comparator_value)))  #thresholding 
    #result = result.filter(result.distances_skl <= 100)  
    #result = result.filter(result.distances_skl != np.inf) 
    #result = result.filter(result.distances_skl == np.inf or result.distances_skl >= 100 or np.isnan(result.distances_skl))   
    return result

def get_neighbors_mfcc_js(song, featureDF):
    comparator_value = song[0]["mfccSkl"]
    distance_udf = F.udf(lambda x: float(jensen_shannon(x, comparator_value)), DoubleType())
    result = featureDF.withColumn('distances_js', distance_udf(F.col('mfccSkl'))).select("id", "distances_js")
    #result = result.filter(result.distances_js != np.inf)    
    return result

def get_neighbors_mfcc_euclidean(song, featureDF):
    comparator_value = song[0]["mfccSkl"]
    distance_udf = F.udf(lambda x: float(get_euclidean_mfcc(x, comparator_value)), FloatType())
    result = featureDF.withColumn('distances_mfcc', distance_udf(F.col('mfccSkl'))).select("id", "distances_mfcc")
    return result

def get_nearest_neighbors(song, outname):
    tic1 = int(round(time.time() * 1000))
    song = fullFeatureDF.filter(featureDF.id == song).collect()#
    tac1 = int(round(time.time() * 1000))
    time_dict['COMPARATOR: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    neighbors_mfcc_eucl = get_neighbors_mfcc_euclidean(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['MFCC: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    neighbors_mfcc_skl = get_neighbors_mfcc_skl(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['SKL: ']= tac1 - tic1
    tic1 = int(round(time.time() * 1000))
    neighbors_mfcc_js = get_neighbors_mfcc_js(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['JS: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))

    mergedSim = neighbors_mfcc_eucl.join(neighbors_mfcc_skl, on=['id'], how='inner').persist()
    mergedSim = mergedSim.join(neighbors_mfcc_js, on=['id'], how='inner').dropDuplicates().persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['JOIN: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    
    neighbors_mfcc_eucl.unpersist()
    neighbors_mfcc_skl.unpersist()
    neighbors_mfcc_js.unpersist()
    mergedSim.unpersist()

    tac1 = int(round(time.time() * 1000))
    time_dict['AGG: ']= tac1 - tic1
    return mergedSim

if len (sys.argv) < 2:
    #song1 = "music/Classical/Katrine_Gislinge-Fr_Elise.mp3" #1517 artists
    song1 = "music/Ooby_Dooby/roy_orbison+Black_and_White_Night+05-Ooby_Dooby.mp3"
    #song2 = "music/Rock & Pop/Sabaton-Primo_Victoria.mp3" #1517 artists
    song2 = "music/Let_It_Be/beatles+Let_It_Be+06-Let_It_Be.mp3"
else: 
    song1 = sys.argv[1]
    song2 = sys.argv[1]

song1 = song1.replace(";","").replace(".","").replace(",","").replace(" ","")#.encode('utf-8','replace')
song2 = song2.replace(";","").replace(".","").replace(",","").replace(" ","")#.encode('utf-8','replace')

tic1 = int(round(time.time() * 1000))
res1 = get_nearest_neighbors(song1, "BUGFIX1.csv").persist()
tac1 = int(round(time.time() * 1000))
time_dict['MERGED_FULL_SONG1: ']= tac1 - tic1

tic2 = int(round(time.time() * 1000))
res2 = get_nearest_neighbors(song2, "BUGFIX2.csv").persist()
tac2 = int(round(time.time() * 1000))
time_dict['MERGED_FULL_SONG2: ']= tac2 - tic2

total2 = int(round(time.time() * 1000))
time_dict['MERGED_TOTAL: ']= total2 - total1

tic1 = int(round(time.time() * 1000))
res1.toPandas().to_csv("BUGFIX1.csv", encoding='utf-8')
res1.unpersist()
tac1 = int(round(time.time() * 1000))
time_dict['CSV1: ']= tac1 - tic1

tic2 = int(round(time.time() * 1000))
res2.toPandas().to_csv("BUGFIX2.csv", encoding='utf-8')
res2.unpersist()
tac2 = int(round(time.time() * 1000))
time_dict['CSV2: ']= tac2 - tic2

print time_dict
print "\n\n"

debug_dict['Negative JS: ']= negjs.value
debug_dict['Nan JS: ']= nanjs.value
debug_dict['Non Positive Definite JS: ']= nonpdjs.value
debug_dict['Negative SKL: ']= negskl.value
debug_dict['Nan SKL: ']= nanskl.value
debug_dict['Non Invertible SKL: ']= noninskl.value

print debug_dict

featureDF.unpersist()


