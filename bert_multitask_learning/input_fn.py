

from functools import partial

import tensorflow as tf

from .bert_preprocessing.bert_utils import (add_special_tokens_with_seqs,
                                            create_mask_and_padding,
                                            tokenize_text_with_seqs,
                                            truncate_seq_pair)
from .bert_preprocessing.create_bert_features import (
    create_bert_features, create_multimodal_bert_features)
from .bert_preprocessing.tokenization import FullTokenizer
from .read_write_tfrecord import read_tfrecord, write_tfrecord
from .special_tokens import EVAL, PREDICT, TRAIN
from .utils import cluster_alphnum, infer_shape_and_type_from_dict

import pandas as pd
import numpy as np


def element_length_func(yield_dict):
    return tf.shape(yield_dict['input_ids'])[0]


def train_eval_input_fn(params, mode='train'):
    '''Train and eval input function of estimator.
    This function will write and read tf record for training
    and evaluation.

    Usage:
        def train_input_fn(): return train_eval_input_fn(params)
        estimator.train(
            train_input_fn, max_steps=params.train_steps, hooks=[train_hook])

    Arguments:
        params {Params} -- Params objects

    Keyword Arguments:
        mode {str} -- ModeKeys (default: {'train'})

    Returns:
        tf Dataset -- Tensorflow dataset
    '''
    write_tfrecord(params=params)

    dataset_dict = read_tfrecord(params=params, mode=mode)

    dataset = tf.data.experimental.sample_from_datasets(
        [ds for _, ds in dataset_dict.items()])

    if mode == 'train':
        dataset = dataset.shuffle(params.shuffle_buffer)

    dataset = dataset.prefetch(tf.data.experimental.AUTOTUNE)
    if params.dynamic_padding:
        dataset = dataset.apply(
            tf.data.experimental.bucket_by_sequence_length(
                element_length_func=element_length_func,
                bucket_batch_sizes=params.bucket_batch_sizes,
                bucket_boundaries=params.bucket_boundaries,
            ))
    else:
        if mode == 'train':
            dataset = dataset.batch(params.batch_size)
        else:
            dataset = dataset.batch(params.batch_size*2)

    return dataset


def predict_input_fn(input_file_or_list, config, mode=PREDICT):
    '''Input function that takes a file path or list of string and 
    convert it to tf.dataset

    Example:
        predict_fn = lambda: predict_input_fn('test.txt', params)
        pred = estimator.predict(predict_fn)

    Arguments:
        input_file_or_list {str or list} -- file path or list of string
        config {Params} -- Params object

    Keyword Arguments:
        mode {str} -- ModeKeys (default: {PREDICT})

    Returns:
        tf dataset -- tf dataset
    '''

    # if is string, treat it as path to file
    if isinstance(input_file_or_list, str):
        inputs = open(input_file_or_list, 'r', encoding='utf8').readlines()
    else:
        inputs = input_file_or_list

    tokenizer = FullTokenizer(config.vocab_file)
    if isinstance(inputs[0], dict) and 'a' not in inputs[0]:
        part_fn = partial(create_multimodal_bert_features, problem='',
                          label_encoder=None,
                          params=config,
                          tokenizer=tokenizer,
                          mode=mode,
                          problem_type='cls',
                          is_seq=False)
    else:
        part_fn = partial(create_bert_features, problem='',
                          label_encoder=None,
                          params=config,
                          tokenizer=tokenizer,
                          mode=mode,
                          problem_type='cls',
                          is_seq=False)
    data_list = part_fn(example_list=inputs)

    def gen():
        for d in data_list:
            yield d
    output_shapes, output_type = infer_shape_and_type_from_dict(data_list[0])
    dataset = tf.data.Dataset.from_generator(
        gen, output_types=output_type, output_shapes=output_shapes)

    dataset = dataset.padded_batch(
        config.batch_size,
        output_shapes
    )
    # dataset = dataset.batch(config.batch_size*2)

    return dataset


def to_serving_input(input_file_or_list, config, mode=PREDICT, tokenizer=None):
    '''A serving input function that takes input file path or
    list of string and apply BERT preprocessing. This fn will
    return a data dict instead of tf dataset. Used in serving.

    Arguments:
        input_file_or_list {str or list} -- file path of list of str
        config {Params} -- Params

    Keyword Arguments:
        mode {str} -- ModeKeys (default: {PREDICT})
        tokenizer {tokenizer} -- Tokenizer (default: {None})
    '''

    # if is string, treat it as path to file
    if isinstance(input_file_or_list, str):
        inputs = open(input_file_or_list, 'r', encoding='utf8').readlines()
    else:
        inputs = input_file_or_list

    if tokenizer is None:
        tokenizer = FullTokenizer(config.vocab_file)

    data_dict = {}
    for doc in inputs:
        inputs_a = cluster_alphnum(doc)
        tokens, target = tokenize_text_with_seqs(
            tokenizer, inputs_a, None)

        tokens_a, tokens_b, target = truncate_seq_pair(
            tokens, None, target, config.max_seq_len)

        tokens, segment_ids, target = add_special_tokens_with_seqs(
            tokens_a, tokens_b, target)

        input_mask, tokens, segment_ids, target = create_mask_and_padding(
            tokens, segment_ids, target, config.max_seq_len)

        input_ids = tokenizer.convert_tokens_to_ids(tokens)
        data_dict['input_ids'] = input_ids
        data_dict['input_mask'] = input_mask
        data_dict['segment_ids'] = segment_ids
        yield data_dict


def serving_input_fn():
    features = {
        'input_ids': tf.placeholder(tf.int32, [None, None]),
        'input_mask': tf.placeholder(tf.int32, [None, None]),
        'segment_ids': tf.placeholder(tf.int32, [None, None])
    }
    return tf.estimator.export.ServingInputReceiver(features, features)
