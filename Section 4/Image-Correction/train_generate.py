import os
import time
import numpy as np
import tensorflow as tf
from glob import glob
from dcgan import DCGAN
from utils import *

FLAGS = tf.app.flags.FLAGS
tf.app.flags.DEFINE_string('data_dir',      'data',                  """Path to tfrecords data directory""")
tf.app.flags.DEFINE_string('log_dir',       'checkpoints',           """Path to write logs and checkpoints""")
tf.app.flags.DEFINE_string('images_dir',    'lfw-gen',                   """Path to save generated images""")
tf.app.flags.DEFINE_string('masktype',      'center',                """Mask types: center, random""")
tf.app.flags.DEFINE_integer('max_itr',      25001,                  """Maximum number of iterations""")
tf.app.flags.DEFINE_integer('batch_size',   128,                     """Batch size""")
tf.app.flags.DEFINE_integer('latest_ckpt',  0,                       """Latest checkpoint timestamp to load""")
tf.app.flags.DEFINE_integer('nb_channels',  3,                       """Number of color channels""")
tf.app.flags.DEFINE_boolean('is_train',     True,                    """False for generating only""")
tf.app.flags.DEFINE_integer('num_examples_per_epoch_for_train', 300, """number of examples for train""")

CROP_IMAGE_SIZE = 96

def read_decode(data_dir, batch_size, s_size):
    files = [os.path.join(data_dir, f) for f in os.listdir(data_dir) if f.endswith('.tfrecords')]
    fqueue = tf.train.string_input_producer(files)
    reader = tf.TFRecordReader()
    _, serialized = reader.read(fqueue)
    features = tf.parse_single_example(serialized, features={
        'height': tf.FixedLenFeature([], tf.int64),
        'width': tf.FixedLenFeature([], tf.int64),
        'image_raw': tf.FixedLenFeature([], tf.string)})

    image = tf.cast(tf.decode_raw(features['image_raw'], tf.uint8), tf.float32)
    height = tf.cast(features['height'], tf.int32)
    width = tf.cast(features['width'], tf.int32)

    image = tf.reshape(image, [height, width, FLAGS.nb_channels])
    image = tf.image.resize_image_with_crop_or_pad(image, CROP_IMAGE_SIZE, CROP_IMAGE_SIZE)
    #image = tf.image.random_flip_left_right(image)

    min_queue_examples = FLAGS.num_examples_per_epoch_for_train
    images = tf.train.shuffle_batch(
        [image],
        batch_size=batch_size,
        capacity=min_queue_examples + FLAGS.nb_channels * batch_size,
        min_after_dequeue=min_queue_examples)
    tf.summary.image('images', images)
    return tf.subtract(tf.div(tf.image.resize_images(images, [s_size * 2 ** 4, s_size * 2 ** 4]), 127.5), 1.0)

def main(_):
    dcgan = DCGAN(batch_size=FLAGS.batch_size, s_size=6, nb_channels=FLAGS.nb_channels)
    traindata = read_decode(FLAGS.data_dir, dcgan.batch_size, dcgan.s_size)
    losses = dcgan.loss(traindata)

    # feature matching
    graph = tf.get_default_graph()
    features_g = tf.reduce_mean(graph.get_tensor_by_name('dg/d/conv4/outputs:0'), 0)
    features_t = tf.reduce_mean(graph.get_tensor_by_name('dt/d/conv4/outputs:0'), 0)
    losses[dcgan.g] += tf.multiply(tf.nn.l2_loss(features_g - features_t), 0.05)

    tf.summary.scalar('g_loss', losses[dcgan.g])
    tf.summary.scalar('d_loss', losses[dcgan.d])
    train_op = dcgan.train(losses, learning_rate=0.0001)
    summary_op = tf.summary.merge_all()

    g_saver = tf.train.Saver(dcgan.g.variables, max_to_keep=15)
    d_saver = tf.train.Saver(dcgan.d.variables, max_to_keep=15)
    g_checkpoint_path = os.path.join(FLAGS.log_dir, 'g.ckpt')
    d_checkpoint_path = os.path.join(FLAGS.log_dir, 'd.ckpt')
    g_checkpoint_restore_path = os.path.join(FLAGS.log_dir, 'g.ckpt-'+str(FLAGS.latest_ckpt))
    d_checkpoint_restore_path = os.path.join(FLAGS.log_dir, 'd.ckpt-'+str(FLAGS.latest_ckpt))

    with tf.Session() as sess:
        summary_writer = tf.summary.FileWriter(FLAGS.log_dir, graph=sess.graph)

        sess.run(tf.global_variables_initializer())
        # restore or initialize generator
        if os.path.exists(g_checkpoint_restore_path+'.meta'):
            print('Restoring variables:')
            for v in dcgan.g.variables:
                print(' ' + v.name)
            g_saver.restore(sess, g_checkpoint_restore_path)

        if FLAGS.is_train:
            # restore or initialize discriminator
            if os.path.exists(d_checkpoint_restore_path+'.meta'):
                print('Restoring variables:')
                for v in dcgan.d.variables:
                    print(' ' + v.name)
                d_saver.restore(sess, d_checkpoint_restore_path)

            # setup for monitoring
            if not os.path.exists(FLAGS.images_dir):
                os.makedirs(FLAGS.images_dir)
            if not os.path.exists(FLAGS.log_dir):
                os.makedirs(FLAGS.log_dir)

            sample_z = sess.run(tf.random_uniform([dcgan.batch_size, dcgan.z_dim], minval=-1.0, maxval=1.0))
            images = dcgan.sample_images(5, 5, inputs=sample_z)

            filename = os.path.join(FLAGS.images_dir, '000000.jpg')
            with open(filename, 'wb') as f:
                f.write(sess.run(images))

            tf.train.start_queue_runners(sess=sess)

            for itr in range(FLAGS.latest_ckpt+1, FLAGS.max_itr):
                start_time = time.time()
                _, g_loss, d_loss = sess.run([train_op, losses[dcgan.g], losses[dcgan.d]])
                duration = time.time() - start_time
		f1=open('./console.log', 'w+')
                print>>f1, ('step: %d, loss: (G: %.8f, D: %.8f), time taken: %.3f' % (itr, g_loss, d_loss, duration))

                if itr % 5000 == 0:
                    # Images generated
                    filename = os.path.join(FLAGS.images_dir, '%06d.jpg' % itr)
                    with open(filename, 'wb') as f:
                        f.write(sess.run(images))

                    # Summary
                    summary_str = sess.run(summary_op)
                    summary_writer.add_summary(summary_str, itr)

                    # Checkpoints
                    g_saver.save(sess, g_checkpoint_path, global_step=itr)
                    d_saver.save(sess, d_checkpoint_path, global_step=itr)

            else:
            	generated = sess.run(dcgan.sample_images(8, 8))

            if not os.path.exists(FLAGS.images_dir):
                os.makedirs(FLAGS.images_dir)

            filename = os.path.join(FLAGS.images_dir, 'generated_image.jpg')
            with open(filename, 'wb') as f:
                print('write to %s' % filename)
                f.write(generated)

if __name__ == '__main__':
    tf.app.run()

