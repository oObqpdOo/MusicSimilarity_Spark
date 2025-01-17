import pyspark
import pyspark.ml.feature
import pyspark.ml.linalg
import pyspark.ml.param
import pyspark.sql.functions
from pyspark.sql import functions as F
from pyspark.sql.types import FloatType
from pyspark.sql.types import DoubleType
from pyspark.sql.functions import udf
from scipy.spatial import distance
from pyspark.ml.feature import BucketedRandomProjectionLSH
#from pyspark.mllib.linalg import Vectors
from pyspark.ml.param.shared import *
from pyspark.ml.linalg import Vectors, VectorUDT
from pyspark.ml.feature import VectorAssembler
import numpy as np
#import org.apache.spark.sql.functions.typedLit
from pyspark.sql.functions import lit
from pyspark.sql.functions import levenshtein  
from pyspark.sql.functions import col
from pyspark.sql.functions import desc
from pyspark.sql.functions import asc
import scipy as sp
from scipy.signal import butter, lfilter, freqz, correlate2d

from pyspark import SparkContext, SparkConf
from pyspark.sql import SQLContext, Row
from pyspark.sql import SparkSession
confCluster = SparkConf().setAppName("MusicSimilarity Cluster")
confLocal = SparkConf().setMaster("local").setAppName("MusicSimilarity Local")
sc = SparkContext(conf=confCluster)
sqlContext = SQLContext(sc)
spark = SparkSession.builder.master("cluster").appName("MusicSimilarity").getOrCreate()

#song = "music/Jazz & Klassik/Keith Jarret - Creation/02-Keith Jarrett-Part II Tokyo.mp3"    #private
#song = "music/Rock & Pop/Sabaton-Primo_Victoria.mp3"           #1517 artists
#song = "music/HURRICANE1.mp3"              #small testset


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
    chroma1 = chroma1_par.reshape(length1, 12)
    length2 = chroma2_par.size/12
    chroma2 = np.empty([length2,12])
    chroma2 = chroma2_par.reshape(length2, 12)
    corr = sp.signal.correlate2d(chroma1, chroma2, mode='full')
    #transposed_chroma = np.transpose(transposed_chroma)
    #mean_line = transposed_chroma[12]
    #print np.max(corr)
    return np.max(corr)



def chroma_cross_correlate_valid(chroma1_par, chroma2_par):
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
    corr = sp.signal.correlate2d(chroma1, chroma2, mode='same')
    transposed_chroma = corr.transpose()  
    transposed_chroma = transposed_chroma / (min(length1, length2))
    transposed_chroma = transposed_chroma.transpose()
    transposed_chroma = np.transpose(transposed_chroma)
    mean_line = transposed_chroma[6]
    #print np.max(mean_line)
    return np.max(mean_line)



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
    if div <= 0:
        div = div * (-1)
    #print div
    return div



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
    #elem1 = np.trace(cov1 * np.linalg.inv(cov2))
    #elem2 = np.trace(cov2 * np.linalg.inv(cov1))
    #elem3 = np.trace( (np.linalg.inv(cov1) + np.linalg.inv(cov2)) * (mean1 - mean2)**2) 
    d = 13
    div = 0.25 * (np.trace(cov1 * np.linalg.inv(cov2)) + np.trace(cov2 * np.linalg.inv(cov1)) + np.trace( (np.linalg.inv(cov1) + np.linalg.inv(cov2)) * (mean1 - mean2)**2) - 2*d)
    #print div
    return div



#########################################################
#   List to Vector UDF
#

list_to_vector_udf = udf(lambda l: Vectors.dense(l), VectorUDT())

#########################################################
#   Pre- Process RH and RP for Euclidean
#
rp = sc.textFile("features/out[0-9]*.rp")
rp = rp.map(lambda x: x.split(","))
kv_rp= rp.map(lambda x: (x[0], list(x[1:])))
rp_df = spark.createDataFrame(kv_rp, ["id", "rp"])
rp_df = rp_df.select(rp_df["id"],list_to_vector_udf(rp_df["rp"]).alias("rp"))
rh = sc.textFile("features/out[0-9]*.rh")
rh = rh.map(lambda x: x.split(","))
kv_rh= rh.map(lambda x: (x[0], list(x[1:])))
rh_df = spark.createDataFrame(kv_rh, ["id", "rh"])
rh_df = rh_df.select(rh_df["id"],list_to_vector_udf(rh_df["rh"]).alias("rh"))

#########################################################
#   Pre- Process BH for Euclidean
#
bh = sc.textFile("features/out[0-9]*.bh")
bh = bh.map(lambda x: x.split(";"))
kv_bh = bh.map(lambda x: (x[0], x[1], Vectors.dense(x[2].replace(' ', '').replace('[', '').replace(']', '').split(','))))
bh_df = spark.createDataFrame(kv_bh, ["id", "bpm", "bh"])

#########################################################
#   Pre- Process Notes for Levenshtein
#
notes = sc.textFile("features/out[0-9]*.notes")
notes = notes.map(lambda x: x.split(';'))
notes = notes.map(lambda x: (x[0], x[1], x[2], x[3].replace("10",'K').replace("11",'L').replace("0",'A').replace("1",'B').replace("2",'C').replace("3",'D').replace("4",'E').replace("5",'F').replace("6",'G').replace("7",'H').replace("8",'I').replace("9",'J')))
notes = notes.map(lambda x: (x[0], x[1], x[2], x[3].replace(',','').replace(' ','')))
notesDf = spark.createDataFrame(notes, ["id", "key", "scale", "notes"])

#########################################################
#   Pre- Process Chroma for cross-correlation
#
chroma = sc.textFile("features/out[0-9]*.chroma")
chroma = chroma.map(lambda x: x.split(';'))
chromaRdd = chroma.map(lambda x: (x[0],(x[1].replace(' ', '').replace('[', '').replace(']', '').split(','))))
chromaDf = spark.createDataFrame(chromaRdd, ["id", "chroma"])
chromaDf = chromaDf.select(chromaDf["id"],list_to_vector_udf(chromaDf["chroma"]).alias("chroma"))

#########################################################
#   Pre- Process MFCC for SKL and JS
#
mfcc = sc.textFile("features/out[0-9]*.mfcckl")
mfcc = mfcc.map(lambda x: x.split(';'))
meanRdd = mfcc.map(lambda x: (x[0],(x[1].replace(' ', '').replace('[', '').replace(']', '').split(','))))
meanDf = spark.createDataFrame(meanRdd, ["id", "mean"])
meanVec = meanDf.select(meanDf["id"],list_to_vector_udf(meanDf["mean"]).alias("mean"))
#meanVec.first()
covRdd = mfcc.map(lambda x: (x[0],(x[2].replace(' ', '').replace('[', '').replace(']', '').split(','))))
covDf = spark.createDataFrame(covRdd, ["id", "cov"])
covVec = covDf.select(covDf["id"],list_to_vector_udf(covDf["cov"]).alias("cov"))
#covVec.first()
mfccDf = meanVec.join(covVec, on=['id'], how='left_outer')
assembler = VectorAssembler(inputCols=["mean", "cov"],outputCol="mfccSkl")
mfccDfMerged = assembler.transform(mfccDf).select("id", "mfccSkl").dropDuplicates()
#print("Assembled columns 'mean', 'var', 'cov' to vector column 'features'")
#mfccDfMerged.select("features", "id").show(truncate=False)
#mfccDfMerged.first()

#########################################################
#   Pre- Process MFCC for Euclidean
#
mfcceuc = sc.textFile("features/out[0-9]*.mfcc")
mfcceuc = mfcceuc.map(lambda x: x.split(';'))
mfcceuc = mfcceuc.map(lambda x: (x[0], list(x[1:])))
meanRddEuc = mfcceuc.map(lambda x: (x[0],(x[1][0].replace(' ', '').replace('[', '').replace(']', '').split(','))))
meanDfEuc = spark.createDataFrame(meanRddEuc, ["id", "mean"])
meanVecEuc = meanDfEuc.select(meanDfEuc["id"],list_to_vector_udf(meanDfEuc["mean"]).alias("mean"))
varRddEuc = mfcceuc.map(lambda x: (x[0],(x[1][1].replace(' ', '').replace('[', '').replace(']', '').split(','))))
varDfEuc = spark.createDataFrame(varRddEuc, ["id", "var"])
varVecEuc = varDfEuc.select(varDfEuc["id"],list_to_vector_udf(varDfEuc["var"]).alias("var"))
covRddEuc = mfcceuc.map(lambda x: (x[0],(x[1][2].replace(' ', '').replace('[', '').replace(']', '').split(','))))
covDfEuc = spark.createDataFrame(covRddEuc, ["id", "cov"])
covVecEuc = covDfEuc.select(covDfEuc["id"],list_to_vector_udf(covDfEuc["cov"]).alias("cov"))
mfccEucDf = meanVecEuc.join(varVecEuc, on=['id'], how='left_outer')
mfccEucDf = mfccEucDf.join(covVecEuc, on=['id'], how='left_outer')
assembler = VectorAssembler(inputCols=["mean", "var", "cov"],outputCol="mfccEuc")
mfccEucDfMerged = assembler.transform(mfccEucDf).select("id", "mfccEuc").dropDuplicates()

#print(rh_df.count())
#print(rp_df.count())
#print(bh_df.count())
#print(notesDf.count())
#print(chromaDf.count())
#print(mfccDfMerged.count())
#print(mfccEucDfMerged.count())

#########################################################
#   Gather all features in one dataframe
#
featureDF = mfccEucDfMerged.join(mfccDfMerged, on=["id"], how='left_outer')
featureDF = featureDF.join(chromaDf, on=['id'], how='left_outer')
featureDF = featureDF.join(notesDf, on=['id'], how='left_outer')
featureDF = featureDF.join(rp_df, on=['id'], how='left_outer')
featureDF = featureDF.join(rh_df, on=['id'], how='left_outer')
featureDF = featureDF.join(bh_df, on=['id'], how='left_outer').dropDuplicates()
#print(featureDF.count())
featureDF.toPandas().to_csv("featureDF.csv", encoding='utf-8')

#featureDF.saveAsTable("featureDF")

#song = "music/Reggae/Damian Marley - Confrontation.mp3"
#filterDF = featureDF.filter(featureDF.id == song)
#comparator_value_ = Vectors.dense(filterDF.select("mfccEuc").collect())



def get_neighbors(song, outname):
    song = featureDF.filter(featureDF.id == song)

    comparator_value = Vectors.dense(song.select("mfccSkl").collect()[0][0]) 
    distance_udf = F.udf(lambda x: float(symmetric_kullback_leibler(x, comparator_value)), DoubleType())
    neighbors_mfcc_skl = featureDF.withColumn('distances_skl', distance_udf(F.col('mfccSkl'))).select("id", "distances_skl")
    #thresholding 
    neighbors_mfcc_skl = neighbors_mfcc_skl.filter(neighbors_mfcc_skl.distances_skl <= 100)  
    max_val = neighbors_mfcc_skl.agg({"distances_skl": "max"}).collect()[0]
    max_val = max_val["max(distances_skl)"]
    min_val = neighbors_mfcc_skl.agg({"distances_skl": "min"}).collect()[0]
    min_val = min_val["min(distances_skl)"]
    neighbors_mfcc_skl = neighbors_mfcc_skl.withColumn('scaled_skl', (neighbors_mfcc_skl.distances_skl-min_val)/(max_val-min_val)).select("id", "scaled_skl")

    comparator_value = Vectors.dense(song.select("mfccSkl").collect()[0][0])   
    distance_udf = F.udf(lambda x: float(jensen_shannon(x, comparator_value)), DoubleType())
    neighbors_mfcc_js = featureDF.withColumn('distances_js', distance_udf(F.col('mfccSkl'))).select("id", "distances_js")
    neighbors_mfcc_js = neighbors_mfcc_js.filter(neighbors_mfcc_js.distances_js != np.inf)
    max_val = neighbors_mfcc_js.agg({"distances_js": "max"}).collect()[0]
    max_val = max_val["max(distances_js)"]
    min_val = neighbors_mfcc_js.agg({"distances_js": "min"}).collect()[0]
    min_val = min_val["min(distances_js)"]
    neighbors_mfcc_js = neighbors_mfcc_js.withColumn('scaled_js', (neighbors_mfcc_js.distances_js-min_val)/(max_val-min_val))

    comparator_value = Vectors.dense(song.select("rp").collect()[0][0]) 
    distance_udf = F.udf(lambda x: float(distance.euclidean(x, comparator_value)), FloatType())
    neighbors_rp_euclidean = featureDF.withColumn('distances_rp', distance_udf(F.col('rp'))).select("id", "distances_rp")
    max_val = neighbors_rp_euclidean.agg({"distances_rp": "max"}).collect()[0]
    max_val = max_val["max(distances_rp)"]
    min_val = neighbors_rp_euclidean.agg({"distances_rp": "min"}).collect()[0]
    min_val = min_val["min(distances_rp)"]
    neighbors_rp_euclidean = neighbors_rp_euclidean.withColumn('scaled_rp', (neighbors_rp_euclidean.distances_rp-min_val)/(max_val-min_val)).select("id", "scaled_rp")

    comparator_value = Vectors.dense(song.select("rh").collect()[0][0]) 
    distance_udf = F.udf(lambda x: float(distance.euclidean(x, comparator_value)), FloatType())
    neighbors_rh_euclidean = featureDF.withColumn('distances_rh', distance_udf(F.col('rh'))).select("id", "distances_rh")
    max_val = neighbors_rh_euclidean.agg({"distances_rh": "max"}).collect()[0]
    max_val = max_val["max(distances_rh)"]
    min_val = neighbors_rh_euclidean.agg({"distances_rh": "min"}).collect()[0]
    min_val = min_val["min(distances_rh)"]
    neighbors_rh_euclidean = neighbors_rh_euclidean.withColumn('scaled_rh', (neighbors_rh_euclidean.distances_rh-min_val)/(max_val-min_val)).select("id", "scaled_rh")

    comparator_value = Vectors.dense(song.select("bh").collect()[0][0]) 
    distance_udf = F.udf(lambda x: float(distance.euclidean(x, comparator_value)), FloatType())
    neighbors_bh_euclidean = featureDF.withColumn('distances_bh', distance_udf(F.col('bh'))).select("id", "bpm", "distances_bh")
    max_val = neighbors_bh_euclidean.agg({"distances_bh": "max"}).collect()[0]
    max_val = max_val["max(distances_bh)"]
    min_val = neighbors_bh_euclidean.agg({"distances_bh": "min"}).collect()[0]
    min_val = min_val["min(distances_bh)"]
    neighbors_bh_euclidean = neighbors_bh_euclidean.withColumn('scaled_bh', (neighbors_bh_euclidean.distances_bh-min_val)/(max_val-min_val)).select("id", "scaled_bh", "bpm")

    comparator_value = Vectors.dense(song.select("mfccEuc").collect()[0][0])
    distance_udf = F.udf(lambda x: float(distance.euclidean(x, comparator_value)), FloatType())
    neighbors_mfcc_eucl = featureDF.withColumn('distances_mfcc', distance_udf(F.col('mfccEuc'))).select("id", "distances_mfcc")
    max_val = neighbors_mfcc_eucl.agg({"distances_mfcc": "max"}).collect()[0]
    max_val = max_val["max(distances_mfcc)"]
    min_val = neighbors_mfcc_eucl.agg({"distances_mfcc": "min"}).collect()[0]
    min_val = min_val["min(distances_mfcc)"]
    neighbors_mfcc_eucl = neighbors_mfcc_eucl.withColumn('scaled_mfcc', (neighbors_mfcc_eucl.distances_mfcc-min_val)/(max_val-min_val)).select("id", "scaled_mfcc")

    comparator_value = song.select("notes").collect()[0][0]
    df_merged = featureDF.withColumn("compare", lit(comparator_value))
    df_levenshtein = df_merged.withColumn("distances_levenshtein", levenshtein(col("notes"), col("compare")))
    #df_levenshtein.sort(col("word1_word2_levenshtein").asc()).show()    
    neighbors_notes = df_levenshtein.select("id", "key", "scale", "distances_levenshtein")
    max_val = neighbors_notes.agg({"distances_levenshtein": "max"}).collect()[0]
    max_val = max_val["max(distances_levenshtein)"]
    min_val = neighbors_notes.agg({"distances_levenshtein": "min"}).collect()[0]
    min_val = min_val["min(distances_levenshtein)"]
    neighbors_notes = neighbors_notes.withColumn('scaled_levenshtein', (neighbors_notes.distances_levenshtein-min_val)/(max_val-min_val)).select("id", "scaled_levenshtein", "key", "scale")
    
    comparator_value = Vectors.dense(song.select("chroma").collect()[0][0])
    distance_udf = F.udf(lambda x: float(chroma_cross_correlate_valid(x, comparator_value)), DoubleType())
    neighbors_chroma = featureDF.withColumn('distances_corr', distance_udf(F.col('chroma'))).select("id", "distances_corr")
    max_val = neighbors_chroma.agg({"distances_corr": "max"}).collect()[0]
    max_val = max_val["max(distances_corr)"]
    min_val = neighbors_chroma.agg({"distances_corr": "min"}).collect()[0]
    min_val = min_val["min(distances_corr)"]

    neighbors_chroma = neighbors_chroma.withColumn('scaled_corr', 1 - (neighbors_chroma.distances_corr-min_val)/(max_val-min_val)).select("id", "scaled_corr")
    mergedSim = neighbors_mfcc_eucl.join(neighbors_rp_euclidean, on=['id'], how='inner')
    mergedSim = mergedSim.join(neighbors_bh_euclidean, on=['id'], how='inner')
    mergedSim = mergedSim.join(neighbors_rh_euclidean, on=['id'], how='inner')
    mergedSim = mergedSim.join(neighbors_notes, on=['id'], how='inner')
    mergedSim = mergedSim.join(neighbors_chroma, on=['id'], how='inner')
    mergedSim = mergedSim.join(neighbors_mfcc_skl, on=['id'], how='inner')
    mergedSim = mergedSim.join(neighbors_mfcc_js, on=['id'], how='inner').dropDuplicates()

    #mergedSim = mergedSim.withColumn('aggregated', (mergedSim.scaled_levenshtein + mergedSim.scaled_rp + mergedSim.scaled_mfcc) / 3)
    mergedSim = mergedSim.withColumn('aggregated', (mergedSim.scaled_levenshtein + mergedSim.scaled_mfcc + mergedSim.scaled_corr + mergedSim.scaled_bh + mergedSim.scaled_rp + mergedSim.scaled_skl + mergedSim.scaled_js + mergedSim.scaled_rh) / 8)
    mergedSim = mergedSim.orderBy('aggregated', ascending=True)#.rdd.flatMap(list).collect()
    mergedSim.show()
    out_name = outname#"output.csv"
    mergedSim.toPandas().to_csv(out_name, encoding='utf-8')

songname = "music/Reggae/Damian Marley - Confrontation.mp3"
get_neighbors(songname, "Reggae.csv")

song = "music/Soundtrack/Flesh And Bone - Dakini_ Movement IV.mp3"
get_neighbors(song, "Soundtrack.csv")

song = "music/Hip Hop/Kid Cudi - Mr Rager.mp3"
get_neighbors(song, "HipHop.csv")

song = "music/Metal/Gojira - Global warming.mp3"
get_neighbors(song, "Metal.csv")

song = "music/Electronic/Dynatron - Pulse Power.mp3"
get_neighbors(song, "Electronic.csv")



