import os
import os.path as osp
import pickle
import time

import torch

from model import TiSASRec
from utils import *

if not os.path.isdir(args.dataset + '_' + args.train_dir):
    os.makedirs(args.dataset + '_' + args.train_dir)
with open(os.path.join(args.dataset + '_' + args.train_dir, 'args.txt'),
          'w') as f:
    f.write('\n'.join([str(k) + ',' + str(v) for k, v in
                       sorted(vars(args).items(), key=lambda x: x[0])]))
f.close()

path = osp.join(args.data_dir, f"dataset_{args.dataset}.pt")

if osp.exists(path):
    dataset = torch.load(path)
else:
    dataset = data_partition(args.dataset)
    torch.save(dataset, path)
del path

[user_train, user_valid, user_test, usernum, itemnum, timenum] = dataset
num_batch = len(user_train) // args.batch_size
cc = 0.0
for u in user_train:
    cc += len(user_train[u])
print('average sequence length: %.2f' % (cc / len(user_train)))

f = open(os.path.join(args.dataset + '_' + args.train_dir, 'log.txt'), 'w')

try:
    relation_matrix = pickle.load(open(
        'data/relation_matrix_%s_%d_%d.pickle' % (
            args.dataset, args.maxlen, args.time_span), 'rb'))
except:
    relation_matrix = Relation(user_train, usernum, args.maxlen, args.time_span)
    pickle.dump(relation_matrix, open('data/relation_matrix_%s_%d_%d.pickle' % (
        args.dataset, args.maxlen, args.time_span), 'wb'))

sampler = WarpSampler(user_train, usernum, itemnum, relation_matrix,
                      batch_size=args.batch_size, maxlen=args.maxlen,
                      n_workers=1)
model = TiSASRec(usernum, itemnum, itemnum, args).to(args.device)

for name, param in model.named_parameters():
    try:
        torch.nn.init.xavier_uniform_(param.data)
    except:
        pass  # just ignore those failed init layers

model.train()  # enable model training

epoch_start_idx = 1
if args.state_dict_path is not None:
    try:
        model.load_state_dict(torch.load(args.state_dict_path))
        tail = args.state_dict_path[args.state_dict_path.find('epoch=') + 6:]
        epoch_start_idx = int(tail[:tail.find('.')]) + 1
    except:
        print('failed loading state_dicts, pls check file path: ', end="")
        print(args.state_dict_path)

if args.inference_only:
    model.eval()
    t_test = evaluate(model, dataset, args)
    print('test (NDCG@10: %.4f, HR@10: %.4f)' % (t_test[0], t_test[1]))

bce_criterion = torch.nn.BCEWithLogitsLoss()
adam_optimizer = torch.optim.Adam(model.parameters(), lr=args.lr,
                                  betas=(0.9, 0.98))

T = 0.0
t0 = time.time()

for epoch in range(epoch_start_idx, args.num_epochs + 1):
    if args.inference_only: break  # just to decrease identition
    for step in range(
            num_batch):  # tqdm(range(num_batch), total=num_batch, ncols=70, leave=False, unit='b'):
        u, seq, time_seq, time_matrix, pos, neg = sampler.next_batch()  # tuples to ndarray
        u, seq, pos, neg = np.array(u), np.array(seq), np.array(pos), np.array(
            neg)
        time_seq, time_matrix = np.array(time_seq), np.array(time_matrix)
        pos_logits, neg_logits = model(u, seq, time_matrix, pos, neg)
        pos_labels, neg_labels = torch.ones(pos_logits.shape,
                                            device=args.device), torch.zeros(
            neg_logits.shape, device=args.device)
        # print("\neye ball check raw_logits:"); print(pos_logits); print(neg_logits) # check pos_logits > 0, neg_logits < 0
        adam_optimizer.zero_grad()
        indices = np.where(pos != 0)
        loss = bce_criterion(pos_logits[indices], pos_labels[indices])
        loss += bce_criterion(neg_logits[indices], neg_labels[indices])
        for param in model.item_emb.parameters(): loss += args.l2_emb * torch.norm(
            param)
        for param in model.abs_pos_K_emb.parameters(): loss += args.l2_emb * torch.norm(
            param)
        for param in model.abs_pos_V_emb.parameters(): loss += args.l2_emb * torch.norm(
            param)
        for param in model.time_matrix_K_emb.parameters(): loss += args.l2_emb * torch.norm(
            param)
        for param in model.time_matrix_V_emb.parameters(): loss += args.l2_emb * torch.norm(
            param)
        loss.backward()
        adam_optimizer.step()
        print("loss in epoch {} iteration {}: {}".format(epoch, step,
                                                         loss.item()))  # expected 0.4~0.6 after init few epochs

    if epoch % 20 == 0:
        model.eval()
        t1 = time.time() - t0
        T += t1
        print('Evaluating', end='')
        t_test = evaluate(model, dataset, args)
        t_valid = evaluate_valid(model, dataset, args)
        print(
            'epoch:%d, time: %f(s), valid (NDCG@10: %.4f, HR@10: %.4f), test (NDCG@10: %.4f, HR@10: %.4f)'
            % (epoch, T, t_valid[0], t_valid[1], t_test[0], t_test[1]))

        f.write(str(t_valid) + ' ' + str(t_test) + '\n')
        f.flush()
        t0 = time.time()
        model.train()

    if epoch == args.num_epochs:
        folder = args.dataset + '_' + args.train_dir
        fname = 'TiSASRec.epoch={}.lr={}.layer={}.head={}.hidden={}.maxlen={}.pth'
        fname = fname.format(args.num_epochs, args.lr, args.num_blocks,
                             args.num_heads, args.hidden_units, args.maxlen)
        torch.save(model.state_dict(), os.path.join(folder, fname))

f.close()
sampler.close()
print("Done")
