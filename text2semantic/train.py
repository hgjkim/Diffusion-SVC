import os
import argparse
import torch
from torch.optim import lr_scheduler
from logger import utils
from .utils import get_data_loaders
from diffusion.unit2mel import Unit2Mel, Unit2MelNaive
from diffusion.vocoder import Vocoder
import accelerate
from tools.infer_tools import DiffusionSVC


def parse_args(args=None, namespace=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=True,
        help="path to the config file")
    return parser.parse_args(args=args, namespace=namespace)


if __name__ == '__main__':
    # parse commands
    cmd = parse_args()
    
    # load config
    args = utils.load_config(cmd.config)
    accelerator = accelerate.Accelerator(
        accumulate_grad_batches = args.model.text2semantic.train.accumulate_grad_batches
    )
    device = accelerator.device
    print(' > config:', cmd.config)
    print(' >    exp:', args.env.expdir)
    
    # load vocoder
    # vocoder = Vocoder(args.vocoder.type, args.vocoder.ckpt, device=args.device)
    
    if args.model.text2semantic.type == "roformer":
        from text2semantic.roformer.train import train
        from text2semantic.roformer.roformer import get_model
    else:
        raise ValueError(f" [x] Unknown Model: {args.model.text2semantic.type}")
    
    # load model
    model = get_model(**args.model.text2semantic)
    
    if args.model.text2semantic.train.generate_audio and accelerator.is_main_process:
        diffusion_svc = DiffusionSVC(device=device)  # 加载模型
        diffusion_svc.load_model(model_path=cmd.model, f0_model="fcpe", f0_max=cmd.f0_max, f0_min=cmd.f0_min)
    else:
        diffusion_model = None

    # load parameters
    optimizer = torch.optim.AdamW(model.parameters())
    initial_global_step, model, optimizer = utils.load_model(args.model.text2semantic.train.expdir, model, optimizer, device=device)
    for param_group in optimizer.param_groups:
        param_group['initial_lr'] = args.model.text2semantic.train.lr
        param_group['lr'] = args.model.text2semantic.train.lr * args.model.text2semantic.train.gamma ** max((initial_global_step - 2) // args.model.text2semantic.train.decay_step, 0)
        param_group['weight_decay'] = args.train.weight_decay
    scheduler = lr_scheduler.StepLR(optimizer, step_size=args.model.text2semantic.train.decay_step, gamma=args.model.text2semantic.train.gamma, last_epoch=initial_global_step-2)

    model = model.to(device)
    
    for state in optimizer.state.values():
        for k, v in state.items():
            if torch.is_tensor(v):
                state[k] = v.to(device)
                    
    # datas
    loader_train, loader_valid = get_data_loaders(args, whole_audio=False)
    _, model, optim, scheduler = accelerator.prepare(
        loader_train, model, optimizer, scheduler
    )

    # run
    train(args, initial_global_step, model, optimizer, scheduler, diffusion_model, loader_train, loader_valid, accelerator)
    
