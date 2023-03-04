import os
import time

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import utils
from tqdm import tqdm

from data.data_loader_test import CreateDataLoader
from models.afwm_test import AFWM
from models.rmgn_generator import RMGNGenerator
from options.test_options import TestOptions
from utils.utils import load_checkpoint, Profile


opt = TestOptions().parse()

device = torch.device(f'cuda:{opt.gpu_ids[0]}')

data_loader = CreateDataLoader(opt)
dataset = data_loader.load_data()
dataset_size = len(data_loader)

warp_model = AFWM(opt, 3)
warp_model.eval()
warp_model.to(device)
load_checkpoint(warp_model, opt.warp_checkpoint, device)

gen_model = RMGNGenerator(multilevel=False, predmask=True)
gen_model.eval()
gen_model.to(device)
load_checkpoint(gen_model, opt.gen_checkpoint, device)

tryon_path = os.path.join('results/', opt.name, 'tryon')
warp_path = os.path.join('results/', opt.name, 'warp')
vis_path = os.path.join('results/', opt.name, 'visualize')
os.makedirs(tryon_path, exist_ok=True)
os.makedirs(warp_path, exist_ok=True)
os.makedirs(vis_path, exist_ok=True)
with torch.no_grad():
    seen, dt = 0, (Profile(), Profile(), Profile())
    for idx, data in enumerate(tqdm(dataset)):
        with dt[0]:
            real_image = data['image']
            clothes = data['clothes']
            ##edge is extracted from the clothes image with the built-in function in python
            edge = data['edge']
            edge = torch.FloatTensor((edge.detach().numpy() > 0.5).astype(np.int64))
            clothes = clothes * edge        

        with dt[1]:
            flow_out = warp_model(real_image.to(device), clothes.to(device))
            warped_cloth, last_flow, = flow_out
            warped_edge = F.grid_sample(edge.to(device), last_flow.permute(0, 2, 3, 1),
                                mode='bilinear', padding_mode='zeros', align_corners=opt.align_corners)
        
        with dt[2]:
            #gen_inputs = torch.cat([real_image.to(device), warped_cloth, warped_edge], 1)
            gen_inputs_clothes = torch.cat([warped_cloth, warped_edge], 1)
            gen_inputs_persons = real_image.to(device)
            
            gen_outputs, out_L1, out_L2, M_list = gen_model(gen_inputs_persons, gen_inputs_clothes)

            p_rendered, m_composite = torch.split(gen_outputs, [3, 1], 1)
            p_rendered = torch.tanh(p_rendered)
            m_composite = torch.sigmoid(m_composite)
            m_composite = m_composite * warped_edge
            p_tryon = warped_cloth * m_composite + p_rendered * (1 - m_composite)
        
        seen += len(p_tryon)
        
        ############## Display results ##############
        p_name, c_name = f'{idx}.jpg', f'{idx}.jpg'
        if opt.batchSize==1:
            p_name, c_name = data['p_name'][0],  data['c_name'][0]

        utils.save_image(
            p_tryon,
            os.path.join(tryon_path, p_name),
            nrow=int(1),
            normalize=True,
            value_range=(-1,1),
        )

        utils.save_image(
            warped_cloth,
            os.path.join(warp_path, c_name),
            nrow=int(1),
            normalize=True,
            value_range=(-1,1),
        )

        combine = torch.cat([real_image.float().to(device), clothes.to(device), warped_cloth.to(device), p_tryon], -1).squeeze()
        utils.save_image(
            combine,
            os.path.join(vis_path, p_name),
            nrow=int(1),
            normalize=True,
            value_range=(-1,1),
        )

    ############## FPS ##############
    t = tuple(x.t / seen * 1E3 for x in dt)  # speeds per image
    t = (sum(t), ) + t
    print(f'Speed: %.1fms all, %.1fms pre-process, %.1fms warp, %.1fms gen per image at shape {real_image.size()}' % t)
