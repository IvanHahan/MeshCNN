import argparse
import os
import random

import torch
import pytorch_lightning as pl
from torch.utils.data import Dataset, DataLoader
import glob
import json
import numpy as np
import torchmetrics
from options.pl_options import PLOptions
from data import DataLoader
from models import create_model
from models.losses import postprocess
from models.losses import ce_jaccard
import warnings
from models import networks
from models.mesh_classifier import ClassifierModel
warnings.filterwarnings("ignore")


class MeshSegmenter(pl.LightningModule, ClassifierModel):

    def __init__(self, opt):
        pl.LightningModule.__init__(self)
        ClassifierModel.__init__(self, opt)
        if opt.from_pretrained is not None:
            print('Loaded pretrained weights:', opt.from_pretrained)
            self.load_weights(opt.from_pretrained)
        if self.training:
            self.train_metrics = torch.nn.ModuleList([
                torchmetrics.Accuracy(num_classes=opt.nclasses, average='macro'),
                torchmetrics.IoU(num_classes=opt.nclasses),
                torchmetrics.F1(num_classes=opt.nclasses, average='macro')
            ])
            self.val_metrics = torch.nn.ModuleList([
                torchmetrics.Accuracy(num_classes=opt.nclasses, average='macro'),
                torchmetrics.IoU(num_classes=opt.nclasses),
                torchmetrics.F1(num_classes=opt.nclasses, average='macro')
            ])

    def step(self, batch, metrics, metric_prefix=''):
        out = self.forward(batch)
        true, pred = postprocess(self.model.labels, out)
        loss = self.criterion(true, pred)

        true = true.view(-1)
        pred = pred.argmax(1).view(-1)

        prefix = metric_prefix
        for m in metrics:
            val = m(pred, true)
            metric_name = str(m).split('(')[0]
            self.log(prefix + metric_name.lower(), val, logger=True, prog_bar=True, on_epoch=True)
        self.log(prefix + 'loss', loss, on_epoch=True)
        return loss

    def training_step(self, batch, idx):

        return self.step(batch, self.train_metrics)

    def validation_step(self, batch, idx):
        return self.step(batch, self.val_metrics, metric_prefix='val_')

    def forward(self, batch):
        self.set_input(batch)
        return ClassifierModel.forward(self)

    def on_train_epoch_end(self, unused=None):
        for m in self.train_metrics:
            m.reset()

    def on_validation_epoch_end(self) -> None:
        for m in self.val_metrics:
            m.reset()

    def train_dataloader(self):
        self.opt.phase = 'train'
        return DataLoader(self.opt)

    def val_dataloader(self):
        self.opt.phase = 'test'
        return DataLoader(self.opt)

    def configure_optimizers(self):
        opt = torch.optim.SGD(self.model.net.parameters(), lr=self.opt.lr,
                              momentum=0.9,
                              weight_decay=0.0002)
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, self.opt.max_epochs * 2)
        return [opt], [sched]


if __name__ == '__main__':
    from pytorch_lightning.callbacks import ModelCheckpoint
    args = PLOptions().parse()
    model = MeshSegmenter(args)
    trainer = pl.Trainer.from_argparse_args(args,
                                            callbacks=[ModelCheckpoint(monitor='val_iou',
                                                                       mode='max',
                                                                       save_top_k=3,
                                                                       filename='{epoch:02d}-{val_iou:.2f}',)])
    trainer.fit(model)