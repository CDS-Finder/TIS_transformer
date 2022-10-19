import pytorch_lightning as pl
import torchmetrics as tm
import torch
import numpy as np
import h5py
from torch.utils.data import DataLoader, RandomSampler, SequentialSampler
import torch.nn.functional as F
from pytorch_lightning.callbacks.early_stopping import EarlyStopping

class DeepGSR(pl.LightningModule):
    def __init__(self, lr):
        super().__init__()
        self.save_hyperparameters()

        self.val_rocauc = tm.AUROC(pos_label=1, compute_on_step=False)
        self.val_prauc = tm.AveragePrecision(pos_label=1, compute_on_step=False)
        
        self.test_rocauc = tm.AUROC(pos_label=1, compute_on_step=False)
        self.test_prauc = tm.AveragePrecision(pos_label=1, compute_on_step=False)
        
        self.emb = torch.nn.Embedding.from_pretrained(torch.eye(64), freeze=True)
        
        self.activation = torch.nn.ReLU()
        self.conv2d_1 = torch.nn.Conv2d(1, 50, (30,32), padding='same')
        self.max_pooling2d_1 = torch.nn.MaxPool2d((1,2))
        self.conv2d_2 = torch.nn.Conv2d(50, 100, (10,8))
        self.max_pooling2d_2 = torch.nn.MaxPool2d((1,2))
        self.dropout_1 = torch.nn.Dropout(0.1)
        self.feat_ext = torch.nn.Sequential(self.conv2d_1, self.activation, self.max_pooling2d_1,
                                            self.conv2d_2, self.activation, self.max_pooling2d_2,
                                            self.dropout_1)

        self.dense_1 = torch.nn.Linear(706800,256)
        self.dropout_2 = torch.nn.Dropout(0.1)
        self.dense_2 = torch.nn.Linear(256,2)
        self.feat_learn = torch.nn.Sequential(self.dense_1, self.activation, self.dropout_2, 
                                              self.dense_2)

    def forward(self, x):
        x = self.emb(x).unsqueeze(1)
        x = self.feat_ext(x)
        x = x.view(-1, 706800)
        x = self.feat_learn(x)
        
        return x

    def training_step(self, batch, index):
        x, y_true = batch
        y_hat = self(x)
        
        loss = F.cross_entropy(y_hat, y_true)
        self.log('train_loss', loss)

        return loss
        
    def validation_step(self, batch, index):
        x, y_true = batch
        y_hat = self(x)
        
        self.val_prauc(F.softmax(y_hat, dim=1)[:,1], y_true)
        self.val_rocauc(F.softmax(y_hat, dim=1)[:,1], y_true)
        
        self.log('val_loss', F.cross_entropy(y_hat, y_true))
        self.log('val_prauc', self.val_prauc, on_step=False, on_epoch=True)
        self.log('val_rocauc', self.val_rocauc, on_step=False, on_epoch=True)
                
    def test_step(self, batch, index):
        x, y_true = batch
        y_hat = self(x)
        
        self.test_prauc(F.softmax(y_hat, dim=1)[:,1], y_true)
        self.test_rocauc(F.softmax(y_hat, dim=1)[:,1], y_true)

        self.log('test_loss', F.cross_entropy(y_hat, y_true))
        self.log('test_prauc', self.test_prauc, on_step=False, on_epoch=True)
        self.log('test_rocauc', self.test_rocauc, on_step=False, on_epoch=True)
        
    def configure_optimizers(self):
        optimizer = torch.optim.Adadelta(self.parameters(), lr=self.hparams.lr)

        return optimizer
        
class h5pyDataset(torch.utils.data.Dataset):
    def __init__(self, fh, ):
        super().__init__()
        self.fh = fh
        
    def __len__(self):
        return len(self.fh['sample'])
    
    def __getitem__(self, index):
        # Transformation is performed when a sample is requested
        x = self.fh['sample'][index].astype(int)
        y = self.fh['label'][index].astype(int)
        
        return [x, y]
    
fh = h5py.File('../../data/benchmark_samples.hdf5', 'r')
dataset = h5pyDataset(fh)

val_contigs = [b'2', b'14']
test_contigs = [b'1', b'7', b'13', b'19']

tr_mask = ~np.isin(fh['contig'], val_contigs + test_contigs)
val_mask = np.isin(fh['contig'], val_contigs)
te_mask = np.isin(fh['contig'], test_contigs)

print(f"Training set samples: {tr_mask.sum()}")
print(f"validation set samples: {val_mask.sum()}")
print(f"Testing set samples: {te_mask.sum()}")

idxs = np.arange(len(fh['contig']))

batch_size = 64
epochs = 100

train_dataloader = DataLoader(dataset, batch_size, sampler=RandomSampler(idxs[tr_mask]), num_workers=4)
val_dataloader = DataLoader(dataset, batch_size, sampler=SequentialSampler(idxs[val_mask]), num_workers=4)
test_dataloader = DataLoader(dataset, batch_size, sampler=SequentialSampler(idxs[te_mask]), num_workers=4)


trainer = pl.Trainer(accelerator='gpu', devices=1, auto_scale_batch_size=False, 
                     callbacks=[EarlyStopping(monitor="val_loss", mode="min", patience=10)])
model = DeepGSR(lr=0.001)
trainer.fit(model, train_dataloaders=train_dataloader, val_dataloaders=val_dataloader)
trainer.test(model, dataloaders=test_dataloader, ckpt_path='best')