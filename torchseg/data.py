# Python STL
import os
# Image Processing
import cv2
# PyTorch
import torch
from torch.utils.data import DataLoader, Dataset, sampler
# Data augmentation
from albumentations.augmentations import transforms as T
from albumentations.core.composition import Compose
from albumentations.pytorch import ToTensorV2

# Root folder of dataset
dirname = os.path.dirname(__file__)
DATA_FOLDER = os.path.join(dirname, "dataset/raw/")


# TODO: Generalize binary segmentation to multiclass segmentation
class OrganDataset(Dataset):
    def __init__(self, data_folder, phase, num_classes=2, class_dict=(0, 255)):
        """
        Create an API for the dataset
        :param data_folder: Path to root folder of the dataset
        :param phase: Phase of learning; In ['train', 'val']
        """
        # Root folder of the dataset
        assert os.path.isdir(data_folder), "{} is not a directory or it doesn't exist.".format(data_folder)
        self.root = data_folder

        # Phase of learning
        assert phase in ['train', 'test', 'val'], "Provide any one of train/test/val as phase."
        self.phase = phase

        # Data Augmentations and tensor transformations
        self.transforms = get_transforms(self.phase)

        # Get names & number of images in root/train or root/val
        _path_to_imgs = os.path.join(self.root, self.phase, "imgs")
        assert os.path.isdir(_path_to_imgs), "{} doesn't exist.".format(_path_to_imgs)
        self.image_names = sorted(os.listdir(_path_to_imgs))
        assert len(self.image_names) != 0, "No images found in {}".format(_path_to_imgs)

        # Number of classes in the segmentation target
        assert num_classes >= 2, "Number of classes must be >= 2. 2: Binary, >=2: Multi-class"
        assert isinstance(num_classes, int), "Number of classes must be an integer."
        self.num_classes = num_classes

        # Dictionary specifying the mapping between pixel values [0, 255] and class indices [0, C-1]
        assert len(class_dict) == num_classes, "Length of class dict must be same as number of classes."
        assert max(class_dict) == 255, "Max intensity of grayscale images is 255, " \
                                       "but class dict: {} specifies otherwise".format(class_dict)
        assert min(class_dict) == 0, "Min intensity of grayscale images is 0, " \
                                     "but class dict: {} specifies otherwise".format(class_dict)
        self.class_dict = class_dict

    def __getitem__(self, idx):
        # Load image
        image_name = self.image_names[idx]
        image_path = os.path.join(self.root, self.phase, "imgs", image_name)
        image = cv2.imread(image_path)
        assert image.size != 0, "cv2: Unable to load image - {}".format(image_path)

        # Load mask
        mask_name = image_name
        mask_path = os.path.join(self.root, self.phase, "masks", mask_name)
        # Expect mask to have values in the [0, 255] region corresponding to each class
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)  # <<<< Note: Hardcoded reading in Grayscale
        assert mask.size != 0, "cv2: Unable to load mask - {}".format(mask_path)

        # TODO: Improve this spagetti (ノಠ益ಠ)ノ彡┻━┻
        # Data Augmentation for image and mask
        augmented = self.transforms['aug'](image=image, mask=mask)
        new_image = self.transforms['img_only'](image=augmented['image'])
        new_mask = self.transforms['mask_only'](image=augmented['mask'])
        aug_tensors = self.transforms['final'](image=new_image['image'], mask=new_mask['image'])
        image = aug_tensors['image']
        mask = aug_tensors['mask']

        if self.num_classes == 2:
            mask = torch.unsqueeze(mask, dim=0)  # For [1, H, W] instead of [H, W]
        return image, mask

    def __len__(self):
        return len(self.image_names)


# TODO: Make it easier to add augmentations
# TODO: Add logging here
# TODO: Move into DataSet as static method
def get_transforms(phase):
    """
    Get composed albumentations transforms
    :param phase: Phase of learning; In ['train', 'val']
    :return: Composed list of albumentations transforms
    """
    aug_transforms = []

    if phase == "train":
        # Data augmentation for training only
        aug_transforms.extend([
            T.ShiftScaleRotate(
                shift_limit=0,
                scale_limit=0.1,
                rotate_limit=15,
                p=0.5),
            T.Flip(p=0.5),
            T.RandomRotate90(p=0.5),
        ])
        # Exotic Augmentations for train only 🤤
        aug_transforms.extend([
            T.RandomBrightnessContrast(p=0.5),
            T.ElasticTransform(p=0.5),
            T.MultiplicativeNoise(multiplier=(0.5, 1.5), per_channel=True, p=0.2),
        ])
    aug_transforms.extend([
        T.RandomSizedCrop(min_max_height=(256, 256),
                          height=256,
                          width=256,
                          w2h_ratio=1.0,
                          interpolation=cv2.INTER_LINEAR,
                          p=1.0),
    ])
    aug_transforms = Compose(aug_transforms)

    mask_only_transforms = Compose([
        T.Normalize(mean=0, std=1, always_apply=True)
    ])
    image_only_transforms = Compose([
        T.Normalize(mean=(0.0, 0.0, 0.0), std=(1.0, 1.0, 1.0), always_apply=True)
    ])
    final_transforms = Compose([
        ToTensorV2()
    ])

    transforms = {
        'aug': aug_transforms,
        'img_only': image_only_transforms,
        'mask_only': mask_only_transforms,
        'final': final_transforms
    }
    return transforms


# TODO: Add logging here
def provider(data_folder, phase, batch_size=8, num_workers=4):
    """
    Return DataLoader for the Dataset
    :param data_folder: Path to root folder of the dataset
    :param phase: Phase of learning; In ['train', 'val']
    :param batch_size: Batch size; Usually a multiple of 8
    :param num_workers: Number of workers; Saturate your shared mem
    :return: DataLoader for the provided phase
    """
    image_dataset = OrganDataset(data_folder, phase)
    dataloader = DataLoader(
        image_dataset,
        batch_size=batch_size,
        num_workers=num_workers,
        pin_memory=True,
        shuffle=True,
    )

    return dataloader