#!/usr/bin/env python3

import os
import sys
from io import BytesIO
from subprocess import DEVNULL, PIPE, run

import caffe
import numpy as np

os.environ["GLOG_minloglevel"] = "2"  # seriously :|


class NSFWDetector:

  def __init__(self):

    npath = os.path.join(os.path.dirname(__file__), "nsfw_model")
    self.nsfw_net = caffe.Net(
        os.path.join(npath, "deploy.prototxt"),
        os.path.join(npath, "resnet_50_1by2_nsfw.caffemodel"),
        caffe.TEST,
    )
    self.caffe_transformer = caffe.io.Transformer(
        {"data": self.nsfw_net.blobs["data"].data.shape})
    self.caffe_transformer.set_transpose(
        "data", (2, 0, 1))  # move image channels to outermost
    self.caffe_transformer.set_mean("data", np.array(
        [104, 117, 123]))  # subtract the dataset-mean value in each channel
    self.caffe_transformer.set_raw_scale("data",
                                         255)  # rescale from [0, 1] to [0, 255]
    self.caffe_transformer.set_channel_swap(
        "data", (2, 1, 0))  # swap channels from RGB to BGR

  def _compute(self, img):
    image = caffe.io.load_image(BytesIO(img))

    H, W, _ = image.shape
    _, _, h, w = self.nsfw_net.blobs["data"].data.shape
    h_off = int(max((H - h) / 2, 0))
    w_off = int(max((W - w) / 2, 0))
    crop = image[h_off:h_off + h, w_off:w_off + w, :]

    transformed_image = self.caffe_transformer.preprocess("data", crop)
    transformed_image.shape = (1,) + transformed_image.shape

    input_name = self.nsfw_net.inputs[0]
    output_layers = ["prob"]
    all_outputs = self.nsfw_net.forward_all(blobs=output_layers,
                                            **{input_name: transformed_image})

    outputs = all_outputs[output_layers[0]][0].astype(float)

    return outputs

  def detect(self, fpath):
    try:
      ff = run(
          [
              "ffmpegthumbnailer",
              "-m",
              "-o-",
              "-s256",
              "-t50%",
              "-a",
              "-cpng",
              "-i",
              fpath,
          ],
          stdout=PIPE,
          stderr=DEVNULL,
          check=True,
      )
      image_data = ff.stdout
    except:
      return -1.0

    scores = self._compute(image_data)

    return scores[1]


if __name__ == "__main__":
  n = NSFWDetector()

  for inf in sys.argv[1:]:
    score = n.detect(inf)
    print(inf, score)
