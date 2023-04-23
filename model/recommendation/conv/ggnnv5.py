import torch
import torch.nn as nn
from torch_geometric.nn import Linear
from collections import defaultdict
from torch_scatter.scatter import scatter
#from attention import AttentionModule
#from heterolinear import HeteroLinear
import sys
#from get_data import get_data
import yaml
import math

class AttentionModule(torch.nn.Module):
    def __init__(self, input_dim, num_heads=4, split=1,):
        super().__init__()
        self.input_dim = input_dim
        self.num_heads = num_heads
        self.split = split
        self.out_dim = input_dim
        self.per_dim = input_dim//num_heads

        self.W = torch.nn.ModuleList([Linear(input_dim, self.per_dim, False, weight_initializer='glorot') for _ in range(num_heads)])
        self.q = torch.nn.ParameterList([])
        for _ in range(num_heads):
            q_ =torch.nn.Parameter(torch.zeros(size=(self.per_dim, 1)))
            nn.init.xavier_uniform_(q_.data, gain=1.414)
            self.q.append(q_)
        
        self.LeakyReLU = torch.nn.LeakyReLU(0.2)

    def forward(self, x):
        out = []
        x = x.resize(x.size()[0],self.split, self.input_dim)
        for i in range(self.num_heads):
            W = self.W[i]
            q = self.q[i]
            x_ = W(x)
            att = self.LeakyReLU(torch.matmul(x_, q))
            att = torch.nn.functional.softmax(att, dim=1)
            att = torch.broadcast_to(att, x_.size())
            x_= (x_*att).sum(dim=1)
            out.append(x_)
        return torch.cat(out, dim=1)

class HeteroLinear(torch.nn.Module):
    def __init__(self, in_channels_dict, out_channels):
        super().__init__()
        self.linears = nn.ModuleDict()
        for node_type, in_channels in in_channels_dict.items():
            self.linears[node_type] = Linear(in_channels, out_channels, weight_initializer='glorot')

    def forward(self, x_dict):
        x_dict_out = {}
        for node_type, x in x_dict.items():
            x = self.linears[node_type](x)
            x_dict_out[node_type] = x
        return x_dict_out

class GRUCell(torch.nn.Module):
    def __init__(self, input_size, hidden_size):
        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.Wir = torch.nn.Parameter(torch.rand(input_size, hidden_size))
        self.bir = torch.nn.Parameter(torch.rand(hidden_size))
        self.Whr = torch.nn.Parameter(torch.rand(hidden_size, hidden_size))
        self.bhr = torch.nn.Parameter(torch.rand(hidden_size))
        self.actr = torch.nn.Sigmoid()
        self.Wiz = torch.nn.Parameter(torch.rand(input_size, hidden_size))
        self.biz = torch.nn.Parameter(torch.rand(hidden_size))
        self.Whz = torch.nn.Parameter(torch.rand(hidden_size, hidden_size))
        self.bhz = torch.nn.Parameter(torch.rand(hidden_size))
        self.actz = torch.nn.Sigmoid()
        self.Win = torch.nn.Parameter(torch.rand(input_size, hidden_size))
        self.bin = torch.nn.Parameter(torch.rand(hidden_size))
        self.Whn = torch.nn.Parameter(torch.rand(hidden_size, hidden_size))
        self.bhn = torch.nn.Parameter(torch.rand(hidden_size))
        self.actn = torch.nn.Tanh()
        self.reset_parameters()

    def reset_parameters(self):
        for k,v in self.state_dict().items():
            torch.nn.init.uniform_(v, 0, -1/math.sqrt(self.hidden_size), 1/math.sqrt(self.hidden_size))

    def forward(self, input, hidden):
        r = self.actr(input@self.Wir + self.bir + hidden@self.Whr + self.bhr)
        z = self.actz(input@self.Whz + self.biz + hidden@self.Whz + self.bhz)
        n = self.actn(input@self.Win + self.bin + r * (hidden@self.Whn + self.bhn)) 
        h = z*n + (1-z)*hidden
        return h



class HeteroGGNNConvV5(torch.nn.Module):
    def __init__(self, in_channels_dict, edge_index_dict, out_channels, ReLU):
        super().__init__()

        self.linear = nn.ModuleDict({})
        self.div = defaultdict(int)
        for k in edge_index_dict.keys():
            self.linear['__'.join(k) + '__source'] = Linear(in_channels_dict[k[0]], out_channels, False, weight_initializer='glorot')
            self.linear['__'.join(k) + '__target'] = Linear(in_channels_dict[k[-1]], out_channels, False, weight_initializer='glorot')
            self.div[k[-1]]+=1

        self.gru = nn.ModuleDict({})
        for k in edge_index_dict.keys():
            self.gru['__'.join(k)] = GRUCell(out_channels, out_channels)

        self.gru_meta = nn.ModuleDict({})
        for k in edge_index_dict.keys():
            self.gru_meta['__'.join(k)] = GRUCell(out_channels, out_channels)


        self.ReLU = ReLU

    def forward(self, x_dict, edge_index_dict):
        x_dict_out = {}
        x_dict_out_tmp={}
        aggregate_meta={}
        for k,v in edge_index_dict.items():
            source, target = k[0], k[-1]
            source_x = self.linear['__'.join(k) + '__source'](x_dict[source])
            target_x = self.linear['__'.join(k) + '__target'](x_dict[target])
            source_index = v[0].reshape(-1)
            target_index = v[1].reshape(-1)
            out = torch.zeros_like(target_x).to(target_x.device)
            source_x = source_x[source_index]

            #target_x = target_x + scatter(source_x, target_index, out=out, dim=0, reduce='mean')
            aggregated = scatter(source_x, target_index, out=out, dim=0, reduce='mean')
            target_x =  self.gru['__'.join(k)](target_x, aggregated)
            x_dict_out_tmp[k]=target_x
            if aggregate_meta.get(target)!=None:
                aggregate_meta[target]+aggregated
            else:
                aggregate_meta[target]=aggregated
        x_dict_out_tmp = {k: self.gru['__'.join(k)](v, aggregate_meta[k[-1]]) for k,v in x_dict_out_tmp.items()}
        for k,v in x_dict_out_tmp.items():
            if x_dict_out.get(k[-1])!=None:
                x_dict_out[k[-1]]+=v
            else:
                x_dict_out[k[-1]]=v
        #x_dict_out = {k: self.l2_norm(v) for k,v in x_dict_out.items()}    

        x_dict_out = {k: v/self.div[k] for k,v in x_dict_out.items()}   
        if self.ReLU:
            x_dict_out = {k: v.relu() for k,v in x_dict_out.items()} 
        return x_dict_out
    '''
    def forward(self, x_dict, edge_index_dict):
        x_dict_out = {}
        for node_type in x_dict.keys():
            aggregate_meta = None
            edge_type_list = []
            for edge_type in edge_index_dict.keys():
                if edge_type[-1]==node_type:
                    edge_type_list.append(edge_type)
            x_dict_out_tmp = {}
            for edge_type in edge_type_list:
                source, target = edge_type[0], edge_type[-1]
                source_x = self.linear['__'.join(edge_type)+'__source'](x_dict[source])
                target_x = self.linear['__'.join(edge_type)+'__target'](x_dict[target])
                source_index = edge_index_dict[edge_type][0].reshape(-1)
                target_index = edge_index_dict[edge_type][1].reshape(-1)

                out = torch.zeros_like(target_x).to(target_x.device)
                source_x = source_x[source_index]
                aggregated = scatter(source_x, target_index, out=out, dim=0, reduce='mean')
                if aggregate_meta==None:
                    aggregate_meta=aggregated
                else:
                    aggregate_meta+=aggregated
                target_x =  self.gru['__'.join(edge_type)](aggregated, target_x)
                x_dict_out_tmp[edge_type] = target_x
            aggregate_meta/=self.div[node_type]
            x_dict_out_tmp = {k: self.gru_meta['__'.join(edge_type)](aggregate_meta, v) for k,v in x_dict_out_tmp.items()}
            for out in x_dict_out_tmp.values():
                if x_dict_out.get(node_type)!=None:
                    x_dict_out[node_type] += out
                else:
                    x_dict_out[node_type] = out
            
        x_dict_out = {k: v/self.div[k] for k,v in x_dict_out.items()} 
        if self.ReLU:
            x_dict_out = {k: v.relu() for k,v in x_dict_out.items()} 
        return x_dict_out
        '''


class HeteroGGNNV5(torch.nn.Module):
    def __init__(self, data, config, out_channels=1,multi=True):
        super().__init__()
        self.hidden_channels = config['model']['hidden_channels']
        self.num_layers = config['model']['num_layers']
        self.concat = config['model']['concat']
        self.ReLU = config['model']['ReLU']

        x_dict = data.x_dict
        edge_index_dict = data.edge_index_dict
        self.layers = torch.nn.ModuleList()
        self.multi = multi
        self.first_in_channels_dict = {node_type: x.size(1) for node_type, x in x_dict.items()}
        self.mid_in_channels_dict = {node_type: self.hidden_channels for node_type in x_dict.keys()}
        if multi==True:
            self.att = AttentionModule(input_dim=512, split=5)
            self.first_in_channels_dict['spot'] = 512
       
        self.layers.append(HeteroGGNNConvV5(self.first_in_channels_dict, edge_index_dict, self.hidden_channels, self.ReLU))

        for i in range(self.num_layers-1):
            self.layers.append(HeteroGGNNConvV5(self.mid_in_channels_dict, edge_index_dict, self.hidden_channels, self.ReLU))
        self.linears = HeteroLinear(self.mid_in_channels_dict, out_channels)
        self.multi = multi

    def forward(self, x_dict, edge_index_dict):
        x_dict_all = {node_type: [] for node_type in x_dict.keys()}
        if self.multi==True:
            x_dict['spot'] = self.att(x_dict['spot'])

        for l in self.layers:
            x_dict = l(x_dict, edge_index_dict)
            if not self.concat:continue
            for node_type in x_dict.keys():
                x_dict_all[node_type].append(x_dict[node_type])
        
        out_dict = self.linears(x_dict)
        if self.concat==True:
            #x_dict = {node_type: torch.cat(x, dim=1) for node_type, x in x_dict_all.items()}
            x_dict = {node_type: torch.mean(torch.stack(x, dim=1), dim=1) for node_type, x in x_dict_all.items()}
        return x_dict, out_dict

if __name__=='__main__':
    with open('../config.yaml') as f:
        config = yaml.safe_load(f)

    config['k'] = 20
    config['device'] = 'cuda:0'
    config['explain_num'] = 10
    config['epoch_num'] = 2500
    config['model']['model_type'] = 'ggnn'
    config['model']['num_layers'] = 4
    config['model']['hidden_channels'] = 256
    config['model']['concat'] = True
    config['model']['ReLU'] = True
    config['trainer']['explain_span'] = 50
    config['trainer']['lr'] = 0.0003
    config['trainer']['loss_city_weight'] = 0
    config['trainer']['loss_category_weight'] = 0
    config['trainer']['loss_word_weight'] = 0.2
    config['trainer']['loss_pref_weight'] = 0
    config['trainer']['city_pop_weight']=0
    config['trainer']['spot_pop_weight']=0
    config['data']['word'] = True
    config['data']['city'] = True
    config['data']['category'] = True
    config['data']['pref'] = True
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    data = get_data(category=True, city=True, prefecture=True, multi=True)
    
    model = HeteroGGNNV5(data, config)
    data.to(device)
    model.to(device)
    print(model)
    x_dict, out_dict= model(data.x_dict, data.edge_index_dict)
    print(x_dict)