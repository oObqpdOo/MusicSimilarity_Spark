#!/usr/bin/python
# -*- coding: utf-8 -*-

import pyspark
import pyspark.ml.feature
import pyspark.mllib.linalg
import pyspark.ml.param
import pyspark.sql.functions
from pyspark.sql import functions as F
from pyspark.sql.types import FloatType
from pyspark.sql.types import DoubleType
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
confCluster.set("spark.dynamicAllocation.enabled", "True")
confCluster.set("spark.dynamicAllocation.minExecutors", "15")
confCluster.set("spark.dynamicAllocation.maxExecutors", "15")
confCluster.set("yarn.nodemanager.vmem-check-enabled", "false")
repartition_count = 32

sc = SparkContext(conf=confCluster)
sqlContext = SQLContext(sc)
sc.setLogLevel("ERROR")

time_dict = {}

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

tic1 = int(round(time.time() * 1000))
list_to_vector_udf = udf(lambda l: Vectors.dense(l), VectorUDT())

#########################################################
#   Pre- Process RH and RP for Euclidean
#
rp = sc.textFile("features[0-9]*/out[0-9]*.rp")
rp = rp.map(lambda x: x.split(","))
kv_rp= rp.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), list(x[1:])))
rp_df = sqlContext.createDataFrame(kv_rp, ["id", "rp"])
rp_df = rp_df.select(rp_df["id"],list_to_vector_udf(rp_df["rp"]).alias("rp"))
rh = sc.textFile("features[0-9]*/out[0-9]*.rh")
rh = rh.map(lambda x: x.split(","))
kv_rh= rh.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), list(x[1:])))
rh_df = sqlContext.createDataFrame(kv_rh, ["id", "rh"])
rh_df = rh_df.select(rh_df["id"],list_to_vector_udf(rh_df["rh"]).alias("rh"))

#########################################################
#   Pre- Process BH for Euclidean
#
bh = sc.textFile("features[0-9]*/out[0-9]*.bh")
bh = bh.map(lambda x: x.split(";"))
kv_bh = bh.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), x[1], Vectors.dense(x[2].replace(' ', '').replace('[', '').replace(']', '').split(','))))
bh_df = sqlContext.createDataFrame(kv_bh, ["id", "bpm", "bh"])

#########################################################
#   Pre- Process Notes for Levenshtein
#
notes = sc.textFile("features[0-9]*/out[0-9]*.notes")
notes = notes.map(lambda x: x.split(';'))
notes = notes.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""), x[1], x[2], x[3].replace("10",'K').replace("11",'L').replace("0",'A').replace("1",'B').replace("2",'C').replace("3",'D').replace("4",'E').replace("5",'F').replace("6",'G').replace("7",'H').replace("8",'I').replace("9",'J')))
notes = notes.map(lambda x: (x[0], x[1], x[2], x[3].replace(',','').replace(' ','')))
notesDf = sqlContext.createDataFrame(notes, ["id", "key", "scale", "notes"])

#########################################################
#   Pre- Process Chroma for cross-correlation
#

chroma = sc.textFile("features[0-9]*/out[0-9]*.chroma")
chroma = chroma.map(lambda x: x.replace(' ', '').replace(';', ','))
chroma = chroma.map(lambda x: x.replace('.mp3,', '.mp3;').replace('.wav,', '.wav;').replace('.m4a,', '.m4a;').replace('.aiff,', '.aiff;').replace('.aif,', '.aif;').replace('.au,', '.au;').replace('.flac,', '.flac;').replace('.ogg,', '.ogg;'))
chroma = chroma.map(lambda x: x.split(';'))
#try to filter out empty elements
chroma = chroma.filter(lambda x: (not x[1] == '[]') and (x[1].startswith("[[0.") or x[1].startswith("[[1.")))
chromaRdd = chroma.map(lambda x: (x[0].replace(";","").replace(".","").replace(",","").replace(" ",""),(x[1].replace(' ', '').replace('[', '').replace(']', '').split(','))))
chromaVec = chromaRdd.map(lambda x: (x[0], Vectors.dense(x[1])))
chromaDf = sqlContext.createDataFrame(chromaVec, ["id", "chroma"])

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
featureDF = chromaDf.join(mfccDfMerged, on=["id"], how='inner').persist()
featureDF = featureDF.join(notesDf, on=['id'], how='inner').persist()
featureDF = featureDF.join(rp_df, on=['id'], how='inner').persist()
featureDF = featureDF.join(rh_df, on=['id'], how='inner').persist()
featureDF = featureDF.join(bh_df, on=['id'], how='inner').dropDuplicates().persist()

#Force lazy evaluation to evaluate with an action
#########################################################
#  16 Nodes, 192GB RAM each, 36 cores each (+ hyperthreading = 72)
#   -> max 1152 executors
trans = featureDF.repartition(repartition_count).count()
print(trans)

#already repartitioned
fullFeatureDF = featureDF.persist()
#fullFeatureDF.toPandas().to_csv("featureDF.csv", encoding='utf-8')
tac1 = int(round(time.time() * 1000))
time_dict['PREPROCESS: ']= tac1 - tic1

def get_neighbors_mfcc(song, fullFeatureDF):
    comparator_value = song[0]["mfccSkl"]
    distance_udf = F.udf(lambda x: float(jensen_shannon(x, comparator_value)), DoubleType())
    fullFeatureDF = fullFeatureDF.withColumn('distances_js', distance_udf(F.col('mfccSkl'))).persist()
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_js != np.inf).persist()  
    ##############################
    aggregated = fullFeatureDF.agg(F.min(fullFeatureDF.distances_js),F.max(fullFeatureDF.distances_js),F.mean(fullFeatureDF.distances_js)).persist() 
    mean_val = aggregated.collect()[0]["avg(distances_js)"]
    max_val = aggregated.collect()[0]["max(distances_js)"]
    min_val = aggregated.collect()[0]["min(distances_js)"]
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_js < mean_val).persist()
    fullFeatureDF = fullFeatureDF.withColumn('scaled_js', (fullFeatureDF.distances_js-min_val)/(max_val-min_val)).persist()
    aggregated.unpersist()
    ##############################
    #to be able to drop feature row, perform skl and js and euc directly 
    ##############################
    distance_udf = F.udf(lambda x: float(symmetric_kullback_leibler(x, comparator_value)), DoubleType())
    fullFeatureDF = fullFeatureDF.withColumn('distances_skl', distance_udf(F.col('mfccSkl'))).persist()
    #thresholding 
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_skl <= 10000)  
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_skl != np.inf).persist()       
    ##############################
    aggregated = fullFeatureDF.agg(F.min(fullFeatureDF.distances_skl),F.max(fullFeatureDF.distances_skl),F.mean(fullFeatureDF.distances_skl)).persist() 
    mean_val = aggregated.collect()[0]["avg(distances_skl)"]
    max_val = aggregated.collect()[0]["max(distances_skl)"]
    min_val = aggregated.collect()[0]["min(distances_skl)"]
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_skl < mean_val).persist() 
    fullFeatureDF = fullFeatureDF.withColumn('scaled_skl', (fullFeatureDF.distances_skl-min_val)/(max_val-min_val)).persist()
    aggregated.unpersist()
    ##############################
    #to be able to drop feature row, perform skl and js and euc directly 
    ##############################
    distance_udf = F.udf(lambda x: float(get_euclidean_mfcc(x, comparator_value)), FloatType())
    fullFeatureDF = fullFeatureDF.withColumn('distances_mfcc', distance_udf(F.col('mfccSkl'))).drop("mfccSkl").persist()
    ##############################
    aggregated = fullFeatureDF.agg(F.min(fullFeatureDF.distances_mfcc),F.max(fullFeatureDF.distances_mfcc),F.mean(fullFeatureDF.distances_mfcc)).persist() 
    mean_val = aggregated.collect()[0]["avg(distances_mfcc)"]
    max_val = aggregated.collect()[0]["max(distances_mfcc)"]
    min_val = aggregated.collect()[0]["min(distances_mfcc)"]
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_mfcc < mean_val).persist()
    fullFeatureDF = fullFeatureDF.withColumn('scaled_mfcc', (fullFeatureDF.distances_mfcc-min_val)/(max_val-min_val)).persist()
    aggregated.unpersist()
    return fullFeatureDF

    return fullFeatureDF


def get_neighbors_rp_euclidean(song, fullFeatureDF):
    comparator_value = song[0]["rp"]
    distance_udf = F.udf(lambda x: float(distance.euclidean(x, comparator_value)), FloatType())
    fullFeatureDF = fullFeatureDF.withColumn('distances_rp', distance_udf(F.col('rp'))).drop("rp").persist()
    ##############################
    aggregated = fullFeatureDF.agg(F.min(fullFeatureDF.distances_rp),F.max(fullFeatureDF.distances_rp),F.mean(fullFeatureDF.distances_rp)).persist() 
    mean_val = aggregated.collect()[0]["avg(distances_rp)"]
    max_val = aggregated.collect()[0]["max(distances_rp)"]
    min_val = aggregated.collect()[0]["min(distances_rp)"]
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_rp < mean_val).persist()
    fullFeatureDF = fullFeatureDF.withColumn('scaled_rp', (fullFeatureDF.distances_rp-min_val)/(max_val-min_val)).persist()
    aggregated.unpersist()
    return fullFeatureDF

def get_neighbors_chroma_corr_valid(song, fullFeatureDF):
    comparator_value = song[0]["chroma"]
    distance_udf = F.udf(lambda x: float(chroma_cross_correlate_valid(x, comparator_value)), DoubleType())
    fullFeatureDF = fullFeatureDF.withColumn('distances_corr', distance_udf(F.col('chroma'))).drop("chroma").persist()
    ##############################
    aggregated = fullFeatureDF.agg(F.min(fullFeatureDF.distances_corr),F.max(fullFeatureDF.distances_corr),F.mean(fullFeatureDF.distances_corr)).persist() 
    mean_val = aggregated.collect()[0]["avg(distances_corr)"]
    max_val = aggregated.collect()[0]["max(distances_corr)"]
    min_val = aggregated.collect()[0]["min(distances_corr)"]
    #!!CAREFUL -> CHROMA NOT SMALLER, BUT GREATER THAN MEAN_VAL
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_corr > mean_val).persist()
    fullFeatureDF = fullFeatureDF.withColumn('scaled_chroma', (1 - (fullFeatureDF.distances_corr-min_val)/(max_val-min_val))).persist()
    aggregated.unpersist()
    return fullFeatureDF

def get_neighbors_rh_euclidean(song, fullFeatureDF):
    comparator_value = song[0]["rh"]
    distance_udf = F.udf(lambda x: float(distance.euclidean(x, comparator_value)), FloatType())
    fullFeatureDF = fullFeatureDF.withColumn('distances_rh', distance_udf(F.col('rh'))).drop("rh").persist()
    ##############################
    aggregated = fullFeatureDF.agg(F.min(fullFeatureDF.distances_rh),F.max(fullFeatureDF.distances_rh),F.mean(fullFeatureDF.distances_rh)).persist() 
    mean_val = aggregated.collect()[0]["avg(distances_rh)"]
    max_val = aggregated.collect()[0]["max(distances_rh)"]
    min_val = aggregated.collect()[0]["min(distances_rh)"]
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_rh < mean_val).persist() 
    fullFeatureDF = fullFeatureDF.withColumn('scaled_rh', (fullFeatureDF.distances_rh-min_val)/(max_val-min_val)).persist()
    aggregated.unpersist()
    return fullFeatureDF

def get_neighbors_bh_euclidean(song, fullFeatureDF):
    comparator_value = song[0]["bh"]
    distance_udf = F.udf(lambda x: float(distance.euclidean(x, comparator_value)), FloatType())
    fullFeatureDF = fullFeatureDF.withColumn('distances_bh', distance_udf(F.col('bh'))).drop("bh").persist()
    ##############################
    aggregated = fullFeatureDF.agg(F.min(fullFeatureDF.distances_bh),F.max(fullFeatureDF.distances_bh),F.mean(fullFeatureDF.distances_bh)).persist() 
    mean_val = aggregated.collect()[0]["avg(distances_bh)"]
    max_val = aggregated.collect()[0]["max(distances_bh)"]
    min_val = aggregated.collect()[0]["min(distances_bh)"]
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_bh < mean_val).persist()
    fullFeatureDF = fullFeatureDF.withColumn('scaled_bh', (fullFeatureDF.distances_bh-min_val)/(max_val-min_val)).persist()
    aggregated.unpersist()
    return fullFeatureDF

def get_neighbors_notes(song, fullFeatureDF):
    comparator_value = song[0]["notes"]
    fullFeatureDF = fullFeatureDF.withColumn("compare", lit(comparator_value))
    fullFeatureDF = fullFeatureDF.withColumn("distances_levenshtein", levenshtein(col("notes"), col("compare"))).drop("notes").drop("compare").persist()
    ##############################
    aggregated = fullFeatureDF.agg(F.min(fullFeatureDF.distances_levenshtein),F.max(fullFeatureDF.distances_levenshtein),F.mean(fullFeatureDF.distances_levenshtein)).persist() 
    mean_val = aggregated.collect()[0]["avg(distances_levenshtein)"]
    max_val = aggregated.collect()[0]["max(distances_levenshtein)"]
    min_val = aggregated.collect()[0]["min(distances_levenshtein)"]
    fullFeatureDF = fullFeatureDF.filter(fullFeatureDF.distances_levenshtein < mean_val).persist() 
    fullFeatureDF = fullFeatureDF.withColumn('scaled_notes', (fullFeatureDF.distances_levenshtein-min_val)/(max_val-min_val)).persist()
    aggregated.unpersist()
    return fullFeatureDF

def get_nearest_neighbors_filter_chroma_first(song, outname, fullFeatureDF):
    tic1 = int(round(time.time() * 1000))
    song = fullFeatureDF.filter(featureDF.id == song).collect()#
    tac1 = int(round(time.time() * 1000))
    time_dict['COMP: ']= tac1 - tic1 

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_chroma_corr_valid(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['CHROMA: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_mfcc(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['JS / SKL: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_rp_euclidean(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['RP: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_rh_euclidean(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['RH: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_bh_euclidean(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['BH: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_notes(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['NOTES: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = fullFeatureDF.withColumn('aggregated', (fullFeatureDF.scaled_notes + fullFeatureDF.scaled_mfcc + fullFeatureDF.scaled_chroma + fullFeatureDF.scaled_bh + fullFeatureDF.scaled_rp + fullFeatureDF.scaled_skl + fullFeatureDF.scaled_js + fullFeatureDF.scaled_rh) / 8).persist()
    fullFeatureDF = fullFeatureDF.orderBy('aggregated', ascending=True).persist()#.rdd.flatMap(list).collect()
    fullFeatureDF.show()
    #scaledSim.toPandas().to_csv(outname, encoding='utf-8')

    tac1 = int(round(time.time() * 1000))
    time_dict['AGG_F: ']= tac1 - tic1
    return fullFeatureDF


def get_nearest_neighbors_filter_bh_first (song, outname, fullFeatureDF):
    tic1 = int(round(time.time() * 1000))
    song = fullFeatureDF.filter(featureDF.id == song).collect()#
    tac1 = int(round(time.time() * 1000))
    time_dict['COMP: ']= tac1 - tic1 

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_bh_euclidean(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['BH: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_rh_euclidean(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['RH: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_notes(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['NOTES: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_rp_euclidean(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['RP: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_mfcc(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['MFCC: ']= tac1 - tic1

    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = get_neighbors_chroma_corr_valid(song, fullFeatureDF).persist()
    tac1 = int(round(time.time() * 1000))
    time_dict['CHROMA: ']= tac1 - tic1


    tic1 = int(round(time.time() * 1000))
    fullFeatureDF = fullFeatureDF.withColumn('aggregated', (fullFeatureDF.scaled_notes + fullFeatureDF.scaled_mfcc + fullFeatureDF.scaled_chroma + fullFeatureDF.scaled_bh + fullFeatureDF.scaled_rp + fullFeatureDF.scaled_skl + fullFeatureDF.scaled_js + fullFeatureDF.scaled_rh) / 8).persist()
    fullFeatureDF = fullFeatureDF.orderBy('aggregated', ascending=True).persist()#.rdd.flatMap(list).collect()
    fullFeatureDF.show()
    #scaledSim.toPandas().to_csv(outname, encoding='utf-8')

    tac1 = int(round(time.time() * 1000))
    time_dict['AGG_F: ']= tac1 - tic1
    return fullFeatureDF


song1 = "music/Classical/Katrine_Gislinge-Fr_Elise.mp3"

if len (sys.argv) < 2:
    song1 = "music/Classical/Katrine_Gislinge-Fr_Elise.mp3" #1517 artists
    song1 = "music/Ooby_Dooby/roy_orbison+Black_and_White_Night+05-Ooby_Dooby.mp3"
    song2 = "music/Rock & Pop/Sabaton-Primo_Victoria.mp3" #1517 artists
    song2 = "music/Let_It_Be/beatles+Let_It_Be+06-Let_It_Be.mp3"
else: 
    song1 = sys.argv[1]
    song2 = sys.argv[1]

song1 = song1.replace(";","").replace(".","").replace(",","").replace(" ","")#.encode('utf-8','replace')
song2 = song2.replace(";","").replace(".","").replace(",","").replace(" ","")#.encode('utf-8','replace')


tic1 = int(round(time.time() * 1000))
#reset fullFeatureDF with cached original features
fullFeatureDF = featureDF.persist()
#and then calculate features
res1 = get_nearest_neighbors_filter_chroma_first(song1, "FILTER_REFINE_SONG1_CHROMA_FIRST.csv", fullFeatureDF).persist()
tac1 = int(round(time.time() * 1000))
time_dict['FILTER_FULL_SONG1_CHROMA_FIRST: ']= tac1 - tic1

tic2 = int(round(time.time() * 1000))
#reset fullFeatureDF with cached original features
fullFeatureDF = featureDF.persist()
#and then calculate features
res2 = get_nearest_neighbors_filter_bh_first(song2, "FILTER_REFINE_SONG2_BH_FIRST.csv", fullFeatureDF).persist()
tac2 = int(round(time.time() * 1000))
time_dict['FILTER_FULL_SONG2_BH_FIRST: ']= tac2 - tic2

total2 = int(round(time.time() * 1000))
time_dict['FILTER_TOTAL: ']= total2 - total1

tic1 = int(round(time.time() * 1000))
res1.toPandas().to_csv("FILTER_REFINE_SONG1_CHROMA_FIRST.csv", encoding='utf-8')
res1.unpersist()
tac1 = int(round(time.time() * 1000))
time_dict['CSV1_CHROMA_FIRST: ']= tac1 - tic1

tic2 = int(round(time.time() * 1000))
res2.toPandas().to_csv("FILTER_REFINE_SONG2_BH_FIRST.csv", encoding='utf-8')
res2.unpersist()
tac2 = int(round(time.time() * 1000))
time_dict['CSV2_BH_FIRST: ']= tac2 - tic2

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
fullFeatureDF.unpersist()


