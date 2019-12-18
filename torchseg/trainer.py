# Python STL
import time
import os
# PyTorch
import torch
from torch.optim.lr_scheduler import ReduceLROnPlateau
import torch.optim as optim
import torch.backends.cudnn as cudnn

# Local
from .loss import MixedLoss
from .data import provider
from .data import DATA_FOLDER
from .metrics import Meter

_DIRNAME = os.path.dirname(__file__)
_TIME_FMT = "%I:%M:%S %p"


class Trainer(object):
    """This class takes care of training and validation of our model"""

    def __init__(self, model, args):
        # Set hyperparameters
        self.num_workers = args.num_workers  # Raise this if shared memory is high
        self.batch_size = {"train": args.batch_size, "val": args.batch_size}
        self.lr = args.lr  # See: https://twitter.com/karpathy/status/801621764144971776?lang=en
        self.num_epochs = args.num_epochs
        self.phases = ["train", "val"]

        # Torch-specific initializations
        if not torch.cuda.is_available():
            self.device = torch.device("cpu")
            torch.set_default_tensor_type("torch.FloatTensor")
        else:
            self.device = torch.device("cuda:0")
            torch.set_default_tensor_type("torch.cuda.FloatTensor")
        self.checkpoint_path = os.path.join(_DIRNAME, "checkpoints", args.checkpoint_name)

        # Model, loss, optimizer & scheduler
        self.net = model
        self.net = self.net.to(self.device)  # <<<< Catch: https://pytorch.org/docs/stable/optim.html
        self.criterion = MixedLoss(9.0, 4.0)
        self.optimizer = optim.Adam(self.net.parameters(),
                                    lr=self.lr)  # "Adam is safe" - http://karpathy.github.io/2019/04/25/recipe/
        self.scheduler = ReduceLROnPlateau(self.optimizer, mode="min",
                                           patience=3, verbose=True,
                                           cooldown=0, min_lr=3e-6)

        # Faster convolutions at the expense of memory
        cudnn.benchmark = True

        # Get loaders for training and validation
        self.dataloaders = {
            phase: provider(
                data_folder=DATA_FOLDER,
                phase=phase,
                batch_size=self.batch_size[phase],
                num_workers=self.num_workers,
            )
            for phase in self.phases
        }

        # Initialize losses & scores
        self.best_loss = float("inf")  # Very high best_loss for the first iteration
        self.losses = {phase: [] for phase in self.phases}
        self.iou_scores = {phase: [] for phase in self.phases}
        self.dice_scores = {phase: [] for phase in self.phases}
        self.acc_scores = {phase: [] for phase in self.phases}

    def forward(self, images, targets):
        """Forward pass"""
        images = images.to(self.device)
        masks = targets.to(self.device)
        logits = self.net(images)
        loss = self.criterion(logits, masks)
        return loss, logits

    def iterate(self, epoch, phase):
        """1 epoch in the life of a model"""
        # Initialize meter
        meter = Meter(phase, epoch)
        # Log epoch, phase and start time
        # TODO: Use relative time instead of absolute
        start_time = time.strftime(_TIME_FMT, time.localtime())
        print(f"Starting epoch: {epoch} | phase: {phase} | ⏰: {start_time}")

        # Set up model, loader and initialize losses
        self.net.train(phase == "train")
        batch_size = self.batch_size[phase]
        dataloader = self.dataloaders[phase]
        total_batches = len(dataloader)
        running_loss = 0.0

        # Learning!
        self.optimizer.zero_grad()
        for itr, batch in enumerate(dataloader):
            images, targets = batch
            # Forward pass
            loss, logits = self.forward(images, targets)
            if phase == "train":
                # Backprop for training only
                loss.backward()
                self.optimizer.step()
                self.optimizer.zero_grad()
            # Get losses
            with torch.no_grad():
                running_loss += loss.item()
                logits = logits.detach().cpu()
                meter.update(targets, logits)

        # Calculate losses
        epoch_loss = running_loss / total_batches
        dice, iou, acc, _ = Meter.epoch_log(phase, epoch, epoch_loss,
                                            meter, start_time, _TIME_FMT)
        # Collect losses
        self.losses[phase].append(epoch_loss)
        self.dice_scores[phase].append(dice)
        self.iou_scores[phase].append(iou)
        self.acc_scores[phase].append(acc)

        # Empty GPU cache
        torch.cuda.empty_cache()
        # Return average loss from the criterion for this epoch
        return epoch_loss

    def start(self):
        """Start the loops!"""
        for epoch in range(1, self.num_epochs + 1):    # <<< Change: Hardcoded starting epoch
            # Train model for 1 epoch
            self.iterate(epoch, "train")
            # Construct the state for a possible save later
            state = {
                "epoch": epoch,
                "best_loss": self.best_loss,
                "state_dict": self.net.state_dict(),
                "optimizer": self.optimizer.state_dict(),
            }
            # Validate model for 1 epoch
            if epoch % 5 == 0:  # <<< Change: Hardcoded validation frequency
                val_loss = self.iterate(epoch, "val")
                # Step the scheduler based on validation loss
                self.scheduler.step(val_loss)
                # TODO: Add EarlyStopping
                # TODO: Add model saving on KeyboardInterrupt (^C)

                # Save model if validation loss is lesser than anything seen before
                if val_loss < self.best_loss:
                    print("******** New optimal found, saving state ********")
                    state["best_loss"] = self.best_loss = val_loss
                    # TODO: Add error handling here
                    # TODO: Use a different file for each save
                    # TODO: Sample file name: ./checkpoints/model-e-020-v-0.1234.pth
                    torch.save(state, self.checkpoint_path)
            print()
