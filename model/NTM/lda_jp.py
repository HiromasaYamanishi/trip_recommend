# -*- coding: utf-8 -*-
"""LDA_jp.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/github/m3yrin/NTM/blob/master/LDA_jp.ipynb

# Gensim LDA model for Japanese articles
auther : m3yrin

reference : http://tdual.hatenablog.com/entry/2018/04/09/133000

### Memo
* tdual' s LDA script is massively cited.
* janome tokenizer is used instead of Mecab.

### Dataset
livedoor ニュースコーパス / livedoor News Corpus  
https://www.rondhuit.com/download.html#ldcc  
CC BY-ND 2.1 JP  
https://creativecommons.org/licenses/by-nd/2.1/jp/
"""



import os

import pandas as pd
from urllib import request 
import logging
from pathlib import Path
import numpy as np
import re
import janome
import random
from gensim import corpora, models

from janome.tokenizer import Tokenizer
from janome import analyzer
from janome.charfilter import *
from janome.tokenfilter import *

from tqdm import tqdm
import pickle
tqdm.pandas()

# https://ohke.hateblo.jp/entry/2017/11/02/230000
class NumericReplaceFilter(TokenFilter):
    def apply(self, tokens):
        for token in tokens:
            parts = token.part_of_speech.split(',')
            if (parts[0] == '名詞' and parts[1] == '数'):
                token.surface = '0'
                token.base_form = '0'
                token.reading = 'ゼロ'
                token.phonetic = 'ゼロ'
            yield token

            
class docTokenizer:
    def __init__(self, stopwords, parser=None, include_pos=None, exclude_posdetail=None, exclude_reg=None):
    
        self.stopwords = stopwords
        self.include_pos = include_pos if include_pos else  ["名詞", "動詞", "形容詞"]
        self.exclude_posdetail = exclude_posdetail if exclude_posdetail else ["接尾", "数"]
        self.exclude_reg = exclude_reg if exclude_reg else r"$^"  # no matching reg
        
        self.char_filters = [
                        UnicodeNormalizeCharFilter(), 
                        RegexReplaceCharFilter(r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+", u''), #url
                        RegexReplaceCharFilter(r"\"?([-a-zA-Z0-9.`?{}]+\.jp)\"?", u''), #*.jp
                        RegexReplaceCharFilter(self.exclude_reg, u'')
                       ]
        
        self.token_filters = [
                         NumericReplaceFilter(),
                         POSKeepFilter(self.include_pos),
                         POSStopFilter(self.exclude_posdetail), 
                         LowerCaseFilter()
                        ]
        
        self.analyzer = analyzer.Analyzer(char_filters=self.char_filters, tokenizer=Tokenizer(), token_filters=self.token_filters)
        
    def tokenize(self, text):

        tokens = self.analyzer.analyze(text)
        tokens = [re.sub(r"," ,"\t", str(i)) for i in tokens]
        l = [line.split("\t") for line in tokens]
        
        #Janome response
        #i[] : ['認め', '動詞', '自立', '*', '*', '一段', '連用形', '認める', 'ミトメ', 'ミトメ']

        res = []
        for i in l:
            if i[7] not in self.stopwords:
                res.append(i[7])
                
        return res

"""### Hyper-parameters"""

num_articles = -1
topic_num = 20
passes = 50

"""### Load stopwords"""

res = request.urlopen("http://svn.sourceforge.jp/svnroot/slothlib/CSharp/Version1/SlothLib/NLP/Filter/StopWord/word/Japanese.txt")
stopwords = [line.decode("utf-8").strip() for line in res]
res = request.urlopen("http://svn.sourceforge.jp/svnroot/slothlib/CSharp/Version1/SlothLib/NLP/Filter/StopWord/word/English.txt")
stopwords += [line.decode("utf-8").strip() for line in res]

stopwords += ['*', '&', '[', ']', ')', '(', '-',':','.','/','0', '...?', '——', '!【', '"', ')、', ')。', ')」']

print("# Stopword : ", len(stopwords))

"""### Load articles"""

doc_path = "./text/"
doc_dir = Path(doc_path)
dirs = [i for i in doc_dir.iterdir() if i.is_dir()]
articles = [a for categ in dirs for a in categ.iterdir()]
random.shuffle(articles)

articles = articles[:num_articles]

tokenizer = docTokenizer(stopwords = stopwords, exclude_reg=r"\d(年|月|日)")

df_exp = pd.read_csv('/home/yamanishi/project/trip_recommend/data/jalan/spot/experience_light.csv')
df_review = pd.read_csv('/home/yamanishi/project/trip_recommend/data/jalan/review/review_all.csv')
train_spot = df_exp[df_exp['valid']>=2].loc[:,'spot_name']
valid_spot = df_exp[df_exp['valid']==1].loc[:, 'spot_name']
test_spot = df_exp[df_exp['valid']==0].loc[:, 'spot_name']
df_review_train = df_review[df_review['spot'].isin(train_spot)]
df_review_valid = df_review[df_review['spot'].isin(valid_spot)]
df_review_test = df_review[df_review['spot'].isin(test_spot)]

docs_train = []
for a in tqdm(df_review_train['review']):
    docs_train.append(tokenizer.tokenize(a))

docs_valid = []
for a in tqdm(df_review_valid['review']):
    docs_valid.append(tokenizer.tokenize(a))

docs_test = []
for a in tqdm(df_review_test['review']):
    docs_test.append(tokenizer.tokenize(a))
"""### Build dict"""

# build dict
docs = docs_train+docs_valid+docs_test
d = corpora.Dictionary(docs)
d.filter_extremes(no_below=5, no_above=0.2)
d.compactify()

# make bow
docs_test = docs_valid+docs_test
train_corpus = [d.doc2bow(w) for w in docs_train]
test_corpus =[d.doc2bow(w) for w in docs_test]
# test train split

topic_dict = {'train_corrpus': train_corpus, 'test_corpus':test_corpus, 'docs_test':docs_test, 'd':d}
with open('/home/yamanishi/project/trip_recommend/data/lda/lda.pkl' , 'wb') as f:
    pickle.dump(topic_dict, f)
"""### Build LDA"""

# logging setting
logging.basicConfig(format='%(message)s', level=logging.INFO)
for topic_num in range(5, 16):
# build LDA
    lda = models.LdaModel(corpus=train_corpus, id2word=d,num_topics=topic_num,passes=10, update_every=5)

    """### Results"""

    N = sum(count for doc in train_corpus for id, count in doc)
    print("# of words in train corpas: ",N)
    perplexity = np.exp2(-lda.log_perplexity(train_corpus))
    cm = models.CoherenceModel(model=lda, texts=docs_train, dictionary=d, coherence='u_mass')
    ch = cm.get_coherence()
    print("perplexity(train):", perplexity)
    print("coherence(train):", ch)

    print("==============================")
    N = sum(count for doc in test_corpus for id, count in doc)
    print("# of words in test corpas: ",N)
    perplexity = np.exp2(-lda.log_perplexity(test_corpus))
    cm = models.CoherenceModel(model=lda, texts=docs_test, dictionary=d, coherence='u_mass')
    ch = cm.get_coherence()
    print("perplexity(test):", perplexity)
    print('coherence(test): ', ch)
    with open('lda.txt', 'a') as f:
        f.write(str(topic_num))
        f.write(' ')
        f.write(str(perplexity))
        f.write(' ')
        f.write(str(ch))
        f.write('\n')

    def get_topic_words(topic_id):
        tw = []
        for t in lda.get_topic_terms(topic_id):
            tw.append(d[t[0]])
        
        return tw

    for t in range(topic_num):
        tw = get_topic_words(t)
        print('Topic {}: {}'.format(t + 1, ' '.join(tw)))