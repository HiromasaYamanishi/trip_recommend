import sys
import os
import numpy as np
import random
import pandas as pd
import itertools
import torch
from sklearn.linear_model import Lasso
import yaml
import csv
from tqdm import tqdm
from model import DeepTour

class LIME:
    def __init__(self, data, node_idx, target_edge_type, num_words=15, num_sample=1024, sample=False):
        self.node_idx = node_idx
        self.target_edge_type = target_edge_type
        self.rev_target_edge_type = ('spot', 'relate', 'word')
        self.data = data
        self.target_edge = self.data.edge_index_dict[target_edge_type]
        self.rev_target_edge = self.data.edge_index_dict[self.rev_target_edge_type]
        self.num_sample = num_sample
        self.sample=False
        self.sigma=num_words
        self.alpha=1e-2
        self.sample=sample
        self.num_words = num_words
        self.data_dir = '/home/yamanishi/project/trip_recommend/data/jalan/graph'#self.path.data_graph_dir
        

    def get_onehop_neighor(self, node_idx):
        return self.target_edge[0][self.target_edge[1]==node_idx]

    def get_onehop_edges(self, node_idx):
        perturb_edge = self.target_edge[:, self.target_edge[1]==node_idx]
        not_perturb_edge = self.target_edge[:, self.target_edge[1]!=node_idx]

        perturb_rev_edge = self.rev_target_edge[:, self.rev_target_edge[0]==node_idx]
        not_perturb_rev_edge = self.rev_target_edge[:, self.rev_target_edge[0]!=node_idx]

        return perturb_edge, not_perturb_edge, perturb_rev_edge, not_perturb_rev_edge


    def explain_node(self, model, data, node_idx, device):
        perturb_edge, not_perturb_edge, perturb_rev_edge, not_perturb_rev_edge = self.get_onehop_edges(node_idx)

        vocab_all = np.load(os.path.join(self.data_dir,'tfidf_words.npy'))
        vocab_tmp = vocab_all[perturb_edge[0].cpu().numpy()]
        spot_names = np.load(os.path.join(self.data_dir, 'spot_names.npy'), allow_pickle=True)
        spot_name = spot_names[node_idx] 
        print(spot_name,node_idx, vocab_tmp)

        b = [True, False]
        bool_all = np.array(list(itertools.product(b, repeat=perturb_edge.size()[1])))

        if self.sample:
            ind = np.arange(len(bool_all))
            ind_sample = np.random.choice(ind, size= self.num_sample, replace=False)
            bool_sample = bool_all[ind_sample]
            print(len(bool_sample))
        else:
            bool_sample = bool_all

        model.eval()
        X, y, weights = [],[],[]


        n = perturb_edge.size()[1]
    
        for bool_ in bool_sample:
            l1_norm = sum(bool_.astype(int))
            weight = np.exp(-(n-l1_norm)**2/(self.sigma**2))
            edge_index = torch.cat([torch.tensor(perturb_edge[:, bool_]), torch.tensor(not_perturb_edge)], axis=1).to(device)
            rev_edge_index = torch.cat([torch.tensor(perturb_rev_edge[:,bool_]), torch.tensor(not_perturb_rev_edge)], axis=1).to(device)
            data['word','revrelate','spot'].edge_index = edge_index
            data['spot','relate','word'].edge_index = rev_edge_index

            out = model()[0].cpu().detach().numpy()
            target_out = out[node_idx]

            X.append(list(bool_.astype(int)))
            y.append(target_out.item())
            weights.append(weight)

        X = np.array(X)
        y = np.array(y)
        weights = np.array(weights)
        X = X*(weights.reshape(-1,1))
        y = y*weights

        lasso_model = Lasso(alpha = self.alpha)
        lasso_model.fit(X, y)
        coef = lasso_model.coef_
        return spot_name, vocab_tmp, coef

    def explain_all(self, model, data, device):
        fieldnames=['spot_name','gt','pred']
        for i in range(self.num_words):
            fieldnames.append(f'vocab{i}')
            fieldnames.append(f'coef{i}')

        with open('/home/yamanishi/project/trip_recommend/data/lime/lime_explain_new.csv','w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            writer.writeheader()
            spot_names = np.load(os.path.join(self.data_dir,'spot_names.npy'), allow_pickle=True)
            ground_truth_all = np.load(os.path.join(self.data_dir,'review_counts.npy'))
            model.eval()
            predicted_all = model(data.x_dict, data.edge_index_dict).cpu().detach().numpy()


            limit=len(spot_names)
            for node_idx in tqdm(range(limit)):
                spot_name, vocab, coef = self.explain_node(model, data, node_idx=node_idx, device=device)
                
                d = {'spot_name':spot_name, 'gt':ground_truth_all[node_idx], 'pred':predicted_all[node_idx][0]}
                for i in range(self.num_words):
                    d[f'vocab{i}'] = vocab[i]
                    d[f'coef{i}'] = coef[i]

                writer.writerow(d)

    def explain_random(self, model, data, device, explain_num=100):
        fieldnames=['spot_name','gt','pred']
        for i in range(self.num_words):
            fieldnames.append(f'vocab{i}')
            fieldnames.append(f'coef{i}')

        with open('/home/yamanishi/project/trip_recommend/model/popularity_final/data/lime/lime_explain_new.csv','a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            #writer.writeheader()
            spot_names = np.load(os.path.join(self.data_dir,'spot_names.npy'), allow_pickle=True)
            ground_truth_all = np.load(os.path.join(self.data_dir,'review_counts.npy'))
            model.eval()
            predicted_all = model(data.x_dict, data.edge_index_dict).cpu().detach().numpy()

            spot_idx = np.arange(len(spot_names))
            spot_sample_idx = np.random.choice(spot_idx, size=explain_num, replace=False)
            for node_idx in tqdm(spot_sample_idx):
                spot_name, vocab, coef = self.explain_node(model, data, node_idx=node_idx, device=device)
                
                d = {'spot_name':spot_name, 'gt':ground_truth_all[node_idx], 'pred':predicted_all[node_idx][0]}
                for i in range(self.num_words):
                    d[f'vocab{i}'] = vocab[i]
                    d[f'coef{i}'] = coef[i]

                writer.writerow(d)

    def explain_top(self, model, data, device, explain_num=100):
        print('explain top')
        fieldnames=['spot_name','gt','pred']
        for i in range(self.num_words):
            fieldnames.append(f'vocab{i}')
            fieldnames.append(f'coef{i}')

        with open('/home/yamanishi/project/trip_recommend/model/popularity_final/data/lime/lime_explain_new_word.csv','a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            writer.writeheader()
        spot_names = np.load(os.path.join(self.data_dir,'spot_names.npy'), allow_pickle=True)
        ground_truth_all = np.load(os.path.join(self.data_dir,'review_counts.npy'))
        model.eval()
        predicted_all = model().cpu().detach().numpy()

        spot_top_idx = np.load('/home/yamanishi/project/trip_recommend/data/jalan/spot/top_spot_idx.npy')[:explain_num]
        print(spot_top_idx)
        for node_idx in tqdm(spot_top_idx):
            with open('/home/yamanishi/project/trip_recommend/model/popularity_final/data/lime/lime_explain_new_word.csv','a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)

                spot_name, vocab, coef = self.explain_node(model, data, node_idx=node_idx, device=device)
                
                d = {'spot_name':spot_name, 'gt':ground_truth_all[node_idx], 'pred':predicted_all[node_idx][0]}
                for i in range(self.num_words):
                    d[f'vocab{i}'] = vocab[i]
                    d[f'coef{i}'] = coef[i]

                writer.writerow(d)

    def explain_new(self, model, data, device, explain_num=100):
        print('explain new')
        #data =torch.load('./new_spot/new_spot.pt').to(self.device)
        fieldnames=['spot_name','gt','pred']
        for i in range(self.num_words):
            fieldnames.append(f'vocab{i}')
            fieldnames.append(f'coef{i}')

        with open('/home/yamanishi/project/trip_recommend/model/popularity_final/data/lime/lime_explain_new_word.csv','a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            writer.writeheader()
        spot_names = np.load(os.path.join(self.data_dir,'spot_names.npy'), allow_pickle=True)
        ground_truth_all = np.load(os.path.join(self.data_dir,'review_counts.npy'))
        model.eval()
        predicted_all = model()[0].cpu().detach().numpy()

        spot_top_idx = np.arange(42852, 42912)
        print(spot_top_idx)
        for node_idx in tqdm(spot_top_idx):
            with open('/home/yamanishi/project/trip_recommend/model/popularity_final/data/lime/lime_explain_new_new.csv','a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)

                spot_name, vocab, coef = self.explain_node(model, data, node_idx=node_idx, device=device)
                
                d = {'spot_name':spot_name, 'pred':predicted_all[node_idx][0]}
                for i in range(self.num_words):
                    d[f'vocab{i}'] = vocab[i]
                    d[f'coef{i}'] = coef[i]

                writer.writerow(d)



if __name__=='__main__':
    with open('config.yaml') as f:
        config = yaml.safe_load(f)
    config['data']['word'] = True
    config['data']['category'] = True
    config['data']['city'] = True
    config['data']['station'] = False
    config['data']['prefecture'] = False
    config['data']['spot'] = False
    config['model']['num_layers'] = 3
    config['model']['ReLU'] = True
    config['model']['tpgnn_layers'] = 2
    config['model']['spgnn_layers'] = 2
    config['model']['hidden_channels'] = 256
    config['device'] = 'cuda:0'
    model = DeepTour(config)
    print(model.content_graph)
    model.load_state_dict(torch.load('./data/deeptour.pth'))
    device = 'cuda:0'
    data = model.content_graph
    model.to(device)
    data = model.content_graph
    lime = LIME(node_idx=0, data=data, target_edge_type=('word', 'revrelate', 'spot'), num_words=15, sample=True, num_sample=5000)
    lime.explain_new(model, data, device=device, explain_num=10000)
    #print(spot_name, vocab, coef)

    #print(y)
    #lime.explain_node(model, data, explain_node_id)