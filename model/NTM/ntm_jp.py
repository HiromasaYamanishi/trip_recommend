# -*- coding: utf-8 -*-
"""NTM-jp.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/github/m3yrin/NTM/blob/master/NTM_jp.ipynb

# Pytorch implementation of Gaussian Softmax Neural Topic Model
auther : @m3yrin

### Reference code
* yuewang-cuhk/TAKG
    * https://github.com/yuewang-cuhk/TAKG
* ysmiao/nvdm
    * https://github.com/ysmiao/nvdm/
* http://tdual.hatenablog.com/entry/2018/04/09/133000

### memo
* yuewang-cuhk' s NTM implementation is partially used.
* tdual' s script is massively cited.
* janome tokenizer is used instead of Mecab.

***GPU instance is recommended.***
If training is too slow, please check instance type.

### Dataset
livedoor ニュースコーパス / livedoor News Corpus  
https://www.rondhuit.com/download.html#ldcc  
CC BY-ND 2.1 JP  
https://creativecommons.org/licenses/by-nd/2.1/jp/

### Download Dataset
"""
import os

import re
import random
import json
import time

from urllib import request 
from pathlib import Path
import numpy as np

import gensim
from gensim import corpora, models

import janome
from janome import analyzer
from janome.charfilter import *
from janome.tokenfilter import *
from janome.tokenizer import Tokenizer

import torch
from torch import nn, optim
from torch.nn import functional as F
from torch.utils.tensorboard import SummaryWriter

from sklearn.utils import shuffle
import pandas as pd

from tqdm import tqdm
tqdm.pandas()

"""### Tokenizer"""

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

def _build_bow_vocab(data, bow_vocab_size, stopwords=None):

    bow_dictionary = gensim.corpora.Dictionary(data)
    
    # Remove STOPWORDS
    STOPWORDS = gensim.parsing.preprocessing.STOPWORDS
    if stopwords is not None:
        STOPWORDS = set(STOPWORDS).union(set(stopwords))
    bow_dictionary.filter_tokens(list(map(bow_dictionary.token2id.get, STOPWORDS)))
    
    # Re-id
    bow_dictionary.filter_extremes(no_below=5, no_above=0.2, keep_n=bow_vocab_size)
    bow_dictionary.compactify()
    bow_dictionary.id2token = dict([(id, t) for t, id in bow_dictionary.token2id.items()])
    
    print("BOW dict length : %d" % len(bow_dictionary))
    
    return bow_dictionary

def build_bow_vocab(data, stopwords=None):

    bow_dictionary = gensim.corpora.Dictionary(data)
    
    # Remove STOPWORDS
    STOPWORDS = gensim.parsing.preprocessing.STOPWORDS
    if stopwords is not None:
        STOPWORDS = set(STOPWORDS).union(set(stopwords))
    bow_dictionary.filter_tokens(list(map(bow_dictionary.token2id.get, STOPWORDS)))
    
    # Re-id
    bow_dictionary.filter_extremes(no_below=5, no_above=0.2)
    bow_dictionary.compactify()
    bow_dictionary.id2token = dict([(id, t) for t, id in bow_dictionary.token2id.items()])
    
    print("BOW dict length : %d" % len(bow_dictionary))
    
    return bow_dictionary

"""### Dataloader"""

class DataLoader(object):

    def __init__(self, data, bow_vocab, batch_size, shuffle=True):
        
        self.batch_size = batch_size
        self.bow_vocab = bow_vocab
        
        self.index = 0
        self.pointer = np.array(range(len(data)))
        
        self.data = np.array(data)
        self.bow_data = np.array([bow_vocab.doc2bow(s) for s in data])
        
        # counting total word number
        word_count = []
        for bow in self.bow_data:
            wc = 0
            for (i, c) in bow:
                wc += c
            word_count.append(wc)
        
        self.word_count = sum(word_count)
        self.data_size = len(data)
        
        self.shuffle = shuffle
        self.reset()

    
    def reset(self):
        
        if self.shuffle:
            self.pointer = shuffle(self.pointer)
        
        self.index = 0 
    
    
    # transform bow data into (1 x V) size vector.
    def _pad(self, batch):
        bow_vocab = len(self.bow_vocab)
        res_src_bow = np.zeros((len(batch), bow_vocab))
        
        for idx, bow in enumerate(batch):
            bow_k = [k for k, v in bow]
            bow_v = [v for k, v in bow]
            res_src_bow[idx, bow_k] = bow_v
            
        return res_src_bow
    
    def __iter__(self):
        return self

    def __next__(self):
        
        if self.index >= self.data_size:
            self.reset()
            raise StopIteration()
            
        ids = self.pointer[self.index: self.index + self.batch_size]
        batch = self.bow_data[ids]
        padded = self._pad(batch)
        tensor = torch.tensor(padded, dtype=torch.float, device=device)
        
        self.index += self.batch_size

        return tensor
    
    # for NTM.lasy_predict()
    def bow_and_text(self):
        if self.index >= self.data_size:
            self.reset()
            
        text = self.data[self.index: self.index + self.batch_size]
        batch = self.bow_data[self.index: self.index + self.batch_size]
        padded = self._pad(batch)
        tensor = torch.tensor(padded, dtype=torch.float, device=device)
        self.reset()

        return tensor, text

"""### NTM Model"""

# cited : https://github.com/yuewang-cuhk/TAKG/blob/master/pykp/model.py

class NTM(nn.Module):
    def __init__(self, input_dim, hidden_dim, topic_num,  l1_strength=0.001):
        super(NTM, self).__init__()
        self.input_dim = input_dim
        self.topic_num = topic_num
        self.fc11 = nn.Linear(self.input_dim, hidden_dim)
        self.fc12 = nn.Linear(hidden_dim, hidden_dim)
        self.fc21 = nn.Linear(hidden_dim, topic_num)
        self.fc22 = nn.Linear(hidden_dim, topic_num)
        self.fcs = nn.Linear(self.input_dim, hidden_dim, bias=False)
        self.fcg1 = nn.Linear(topic_num, topic_num)
        self.fcg2 = nn.Linear(topic_num, topic_num)
        self.fcg3 = nn.Linear(topic_num, topic_num)
        self.fcg4 = nn.Linear(topic_num, topic_num)
        self.fcd1 = nn.Linear(topic_num, self.input_dim)
        self.l1_strength = torch.FloatTensor([l1_strength]).to(device)

    def encode(self, x):
        e1 = F.relu(self.fc11(x))
        e1 = F.relu(self.fc12(e1))
        e1 = e1.add(self.fcs(x))
        return self.fc21(e1), self.fc22(e1)

    def reparameterize(self, mu, logvar):
        if self.training:
            std = torch.exp(0.5 * logvar)
            eps = torch.randn_like(std)
            return eps.mul(std).add_(mu)
        else:
            return mu

    def generate(self, h):
        g1 = torch.tanh(self.fcg1(h))
        g1 = torch.tanh(self.fcg2(g1))
        g1 = torch.tanh(self.fcg3(g1))
        g1 = torch.tanh(self.fcg4(g1))
        g1 = g1.add(h)
        return g1

    def decode(self, z):
        d1 = F.softmax(self.fcd1(z), dim=1)
        return d1

    def forward(self, x):
        mu, logvar = self.encode(x.view(-1, self.input_dim))
        z = self.reparameterize(mu, logvar)
        g = self.generate(z)
        return z, g, self.decode(g), mu, logvar

    def print_topic_words(self, vocab_dic, fn, n_top_words=10):
        beta_exp = self.fcd1.weight.data.cpu().numpy().T
        
        print("Writing to %s" % fn)
        fw = open(fn, 'w')
        
        for k, beta_k in enumerate(beta_exp):
            topic_words = [vocab_dic[w_id] for w_id in np.argsort(beta_k)[:-n_top_words - 1:-1]]
            
            print('Topic {}: {}'.format(k, ' '.join(topic_words)))
            
            fw.write('{}\n'.format(' '.join(topic_words)))
        fw.close()

"""### Auxiliary functions"""

def loss_function(recon_x, x, mu, logvar):
    BCE = F.binary_cross_entropy(recon_x, x, size_average=False)
    KLD = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())

    return BCE + KLD

def l1_penalty(para):
    return nn.L1Loss()(para, torch.zeros_like(para))


def check_sparsity(para, sparsity_threshold=1e-3):
    num_weights = para.shape[0] * para.shape[1]
    num_zero = (para.abs() < sparsity_threshold).sum().float()
    return num_zero / float(num_weights)


def update_l1(cur_l1, cur_sparsity, sparsity_target):
    diff = sparsity_target - cur_sparsity
    cur_l1.mul_(2.0 ** diff)

def compute_loss(model, dataloader, optimizer, epoch):
    
    model.train()
    train_loss = 0
    
    for batch_idx, data_bow in enumerate(dataloader):
        data_bow = data_bow.to(device)
        
        # normalize data
        data_bow_norm = F.normalize(data_bow)
        optimizer.zero_grad()
        
        z, g, recon_batch, mu, logvar = model(data_bow_norm)
        
        loss = loss_function(recon_batch, data_bow, mu, logvar)
        loss = loss + model.l1_strength * l1_penalty(model.fcd1.weight)
        loss.backward()
        
        train_loss += loss.item()
        optimizer.step()
    
    sparsity = check_sparsity(model.fcd1.weight.data)
    print("Overall sparsity = %.3f, l1 strength = %.5f" % (sparsity, model.l1_strength))
    print("Target sparsity = %.3f" % target_sparsity)
    update_l1(model.l1_strength, sparsity, target_sparsity)
    
    avg_loss = train_loss / len(dataloader.data)
    
    print('Train epoch: {} Average loss: {:.4f}'.format(
        epoch, avg_loss))
    
    return sparsity, avg_loss

def compute_test_loss(model, dataloader, epoch):
    model.eval()
    test_loss = 0
    with torch.no_grad():
        for i, data_bow in enumerate(dataloader):
            data_bow = data_bow.to(device)
            data_bow_norm = F.normalize(data_bow)

            _, _, recon_batch, mu, logvar = model(data_bow_norm)
            test_loss += loss_function(recon_batch, data_bow, mu, logvar).item()

    avg_loss = test_loss / len(dataloader.data)
    print('Test epoch : {} Average loss: {:.4f}'.format(epoch, avg_loss))
    return avg_loss


def compute_perplexity(model, dataloader):
    
    model.eval()
    loss = 0
    
    with torch.no_grad():
        for i, data_bow in enumerate(dataloader):
            data_bow = data_bow.to(device)
            data_bow_norm = F.normalize(data_bow)
            
            z, g, recon_batch, mu, logvar = model(data_bow_norm)
            
            #loss += loss_function(recon_batch, data_bow, mu, logvar).detach()
            loss += F.binary_cross_entropy(recon_batch, data_bow, size_average=False)
            
    loss = loss / dataloader.word_count
    perplexity = np.exp(loss.cpu().numpy())
    
    return perplexity


def lasy_predict(model, dataloader,vocab_dic, num_example=5, n_top_words=5):
    model.eval()
    docs, text = dataloader.bow_and_text()
    
    docs, text = docs[:num_example], text[:num_example]
    
    docs_device = docs.to(device)
    docs_norm = F.normalize(docs_device)
    z, _, _, _, _ = model(docs_norm)
    z_a = z.detach().cpu().argmax(1).numpy()
    z = torch.softmax(z, dim=1).detach().cpu().numpy()
    
    beta_exp = model.fcd1.weight.data.cpu().numpy().T
    topics = []
    for k, beta_k in enumerate(beta_exp):
        topic_words = [vocab_dic[w_id] for w_id in np.argsort(beta_k)[:-n_top_words - 1:-1]]
        topics.append(topic_words)
    
    for i, (zi, _z_a, t) in enumerate(zip(z, z_a, text)):
        
        print('\n===== # {}, Topic : {}, p : {:.4f} %'.format(i+1, _z_a,  zi[_z_a] * 100))
        print("Topic words :", ', '.join(topics[_z_a]))
        print("Input :", ' '.join(t))
        
def init_weights(m):
    if type(m) == nn.Linear:
        torch.nn.init.kaiming_uniform(m.weight)

"""### Hyper-parameters"""

device = torch.device("cuda:7" if torch.cuda.is_available() else "cpu")

# set random seeds
random.seed(123)
torch.manual_seed(123)

num_articles = -1

# data size limitation
max_src_len = 150
max_trg_len = 10
max_bow_vocab_size=100000

# Model parameter
hidden_dim = 1000 
topic_num = 20
target_sparsity=0.85

# Training parameter
batch_size = 32
learning_rate = 0.001
logdir = "./"
n_epoch = 300

"""### Load Stopwords"""

res = request.urlopen("http://svn.sourceforge.jp/svnroot/slothlib/CSharp/Version1/SlothLib/NLP/Filter/StopWord/word/Japanese.txt")
stopwords = [line.decode("utf-8").strip() for line in res]
res = request.urlopen("http://svn.sourceforge.jp/svnroot/slothlib/CSharp/Version1/SlothLib/NLP/Filter/StopWord/word/English.txt")
stopwords += [line.decode("utf-8").strip() for line in res]

stopwords += ['*', '&', '[', ']', ')', '(', '-',':','.','/','0', '...?', '——', '!【', '"', ')、', ')。', ')」']

print("# Stopwords : ", len(stopwords))

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

train_corpus = [d.doc2bow(w) for w in docs_train]
test_corpus =[d.doc2bow(w) for w in docs_test]

#bow_vocab = build_bow_vocab(docs, bow_vocab_size = max_bow_vocab_size, stopwords = stopwords)
bow_vocab = build_bow_vocab(docs, stopwords = stopwords)
bow_vocab_size=len(bow_vocab)


test_data  = docs_test
valid_data = docs_valid
train_data = docs_train


# Commented out IPython magic to ensure Python compatibility.
# Tensorboard

# %load_ext tensorboard
# %tensorboard --logdir runs

"""### Training"""

# Tensorboard
writer = SummaryWriter()

# building dataloader
dataloader = DataLoader(data = train_data, bow_vocab = bow_vocab, batch_size = batch_size)
dataloader_valid = DataLoader(data = valid_data, bow_vocab = bow_vocab, batch_size = batch_size, shuffle=False)

# builing model and optimiser
ntm_model = NTM(input_dim = bow_vocab_size, hidden_dim = hidden_dim, topic_num = topic_num, l1_strength=0.0000001).to(device)
optimizer = optim.Adam(ntm_model.parameters(), lr=learning_rate)

ntm_model.apply(init_weights)


# Start Training
for epoch in range(1, n_epoch + 1):
    
    print("======== Epoch", epoch, " ========")
    sparsity, train_loss = compute_loss(ntm_model, dataloader, optimizer, epoch)
    val_loss = compute_test_loss(ntm_model, dataloader_valid, epoch)
    
    pp = compute_perplexity(ntm_model, dataloader)
    pp_val = compute_perplexity(ntm_model, dataloader_valid)
    print("PP(train) = %.3f, PP(valid) = %.3f" % (pp, pp_val))
    
    writer.add_scalars('scalar/loss',{'train_loss': train_loss,'valid_loss': val_loss},epoch)
    writer.add_scalars('scalar/perplexity',{'train_pp': pp,'valid_pp': pp_val},epoch)
    writer.add_scalars('scalar/sparsity',{'sparsity': sparsity},epoch)
    writer.add_scalars('scalar/l1_strength',{'l1_strength': ntm_model.l1_strength},epoch)
    
    if epoch % 50 == 0:
        ntm_model.print_topic_words(bow_vocab, os.path.join(logdir, 'topwords_e%d.txt' % epoch))
        lasy_predict(ntm_model, dataloader_valid, bow_vocab, num_example=10, n_top_words=10)
        
writer.close()

"""### Results"""

dataloader_test  = DataLoader(data = test_data, bow_vocab = bow_vocab, batch_size = batch_size, shuffle=False)
pp_test = compute_perplexity(ntm_model, dataloader_test)
print("PP(test) = %.3f" % (pp_test))

ntm_model.print_topic_words(bow_vocab, os.path.join(logdir, 'topwords_e%d.txt' % 9999))

lasy_predict(ntm_model, dataloader_test, bow_vocab, num_example=50, n_top_words=10)