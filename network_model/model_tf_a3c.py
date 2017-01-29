import numpy as np
import os
import random
import math
import time
import threading
import tensorflow as tf
from network_model.model_tf_async import ModelRunnerTFAsync
from network_model.model_tf import Model
            
class ModelRunnerTFA3C(ModelRunnerTFAsync):
    def init_models(self):
        self.model = self.new_model('net-' + str(self.thread_no))
        with tf.device(self.args.device):
            self.a_in = tf.placeholder(tf.float32, shape=[None, self.max_action_no])
            self.v_in = tf.placeholder(tf.float32, shape=[None])
            self.td_in = tf.placeholder(tf.float32, shape=[None])
            self.x_in = self.model.x
            self.y_class = self.model.y_class
            self.v = self.model.v
            
            loss = self.get_loss()
            self.init_gradients(loss, self.model.get_vars())
        
    def new_model(self, name):
        return ModelA3C(self.args, name, True, self.max_action_no, self.thread_no)

    def get_loss(self):
        with tf.device(self.args.device):
            # avoid NaN with clipping when value in pi becomes zero
            log_pi = tf.log(tf.clip_by_value(self.y_class, 1e-20, 1.0))
      
            # policy entropy
            entropy = -tf.reduce_sum(self.y_class * log_pi, reduction_indices=1)
      
            # policy loss (output)  (Adding minus, because the original paper's objective function is for gradient ascent, but we use gradient descent optimizer.)
            policy_loss = - tf.reduce_sum( tf.reduce_sum( tf.mul( log_pi, self.a_in ), reduction_indices=1 ) * self.td_in + entropy * 0.01 )
            value_loss = 0.5 * tf.nn.l2_loss(self.v - self.v_in)
            loss = policy_loss + value_loss
        
        return loss
        
    def predict_action_state(self, state):
        y_class, v = self.sess.run([self.y_class, self.v], {self.x_in: state})
        return y_class[0], v[0]

    def predict_state(self, state):
        v = self.sess.run(self.v, {self.x_in: state})
        return v[0]

    def predict(self, state):
        y_class = self.sess.run(self.y_class, {self.x_in: state})
        return y_class[0]

    def train(self, prestates, v_pres, actions, rewards, terminals, v_post, learning_rate):
        data_len = len(actions)

        action_mat = np.zeros((data_len, self.max_action_no), dtype=np.uint8)
        v_in = np.zeros(data_len)
        td_in = np.zeros(data_len)
        
        R = v_post
        for i in range(data_len):
            action_mat[i, actions[i]] = 1
            if self.args.clip_reward:
                reward = self.clip_reward(rewards[i])
            else:
                reward = rewards[i]
            v_in[i] = reward + self.discount_factor * R
            td_in[i] = v_in[i] - v_pres[i]
            R = v_in[i]

        self.sess.run(self.train_step, feed_dict={
            self.x_in: prestates,
            self.a_in: action_mat,
            self.v_in: v_in,
            self.td_in: td_in
        })
        
        self.sess.run( self.apply_grads,
              feed_dict = { self.global_learning_rate: learning_rate } )
        
        self.sess.run(self.reset_acc_gradients)
        self.sess.run(self.sync)
        
class ModelA3C(Model):
    def build_network_nature(self, name, trainable, num_actions):
        self.print_log("Building network A3C nature for %s trainable=%s" % (name, trainable))
    
        with tf.variable_scope(name):
            x_in = tf.placeholder(tf.uint8, shape=[None, self.screen_height, self.screen_width, self.history_len], name="screens")
            x_normalized = tf.to_float(x_in) / 255.0
            self.print_log(x_normalized)
    
            W_conv1, b_conv1 = self.make_layer_variables([8, 8, self.history_len, 32], trainable, "conv1")
            h_conv1 = tf.nn.relu(tf.nn.conv2d(x_normalized, W_conv1, strides=[1, 4, 4, 1], padding='VALID') + b_conv1, name="h_conv1")
            self.print_log(h_conv1)
    
            W_conv2, b_conv2 = self.make_layer_variables([4, 4, 32, 64], trainable, "conv2")
            h_conv2 = tf.nn.relu(tf.nn.conv2d(h_conv1, W_conv2, strides=[1, 2, 2, 1], padding='VALID') + b_conv2, name="h_conv2")
            self.print_log(h_conv2)
    
            W_conv3, b_conv3 = self.make_layer_variables([3, 3, 64, 64], trainable, "conv3")
            h_conv3 = tf.nn.relu(tf.nn.conv2d(h_conv2, W_conv3, strides=[1, 1, 1, 1], padding='VALID') + b_conv3, name="h_conv3")
            self.print_log(h_conv3)
            
            conv_out_size = np.prod(h_conv3._shape[1:]).value
            h_conv3_flat = tf.reshape(h_conv3, [-1, conv_out_size], name="h_conv3_flat")
            self.print_log(h_conv3_flat)
    
            W_fc1, b_fc1 = self.make_layer_variables([conv_out_size, 512], trainable, "fc1")
            h_fc1 = tf.nn.relu(tf.matmul(h_conv3_flat, W_fc1) + b_fc1, name="h_fc1")
            self.print_log(h_fc1)
    
            W_fc2, b_fc2 = self.make_layer_variables([512, num_actions], trainable, "fc2")
            y = tf.matmul(h_fc1, W_fc2) + b_fc2
            self.print_log(y)
            
            y_class = tf.nn.softmax(y)
            
            W_fc3, b_fc3 = self.make_layer_variables([512, 1], trainable, "fc3")
            v_ = tf.matmul(h_fc1, W_fc3) + b_fc3
            v = tf.reshape(v_, [-1] )
        
        self.x = x_in
        self.y = y
        self.y_class = y_class
        self.v = v        
        tvars = tf.trainable_variables()
        self.variables = [tvar for tvar in tvars if tvar.name.startswith(name)]
        print 'len(self.variables) : %s' % len(self.variables)
        
    def build_network_nips(self, name, trainable, num_actions):
        self.print_log("Building network A3C nips for %s trainable=%s" % (name, trainable))
    
        with tf.variable_scope(name):
            x_in = tf.placeholder(tf.float32, shape=[None, self.screen_height, self.screen_width, self.history_len], name="screens")
            x_normalized = tf.to_float(x_in) / 255.0
            self.print_log(x_normalized)
    
            W_conv1, b_conv1 = self.make_layer_variables([8, 8, self.history_len, 16], trainable, "conv1")
            h_conv1 = tf.nn.relu(tf.nn.conv2d(x_normalized, W_conv1, strides=[1, 4, 4, 1], padding='VALID') + b_conv1, name="h_conv1")
            self.print_log(h_conv1)
    
            W_conv2, b_conv2 = self.make_layer_variables([4, 4, 16, 32], trainable, "conv2")
            h_conv2 = tf.nn.relu(tf.nn.conv2d(h_conv1, W_conv2, strides=[1, 2, 2, 1], padding='VALID') + b_conv2, name="h_conv2")
            self.print_log(h_conv2)

            conv_out_size = np.prod(h_conv2._shape[1:]).value
            h_conv2_flat = tf.reshape(h_conv2, [-1, conv_out_size], name="h_conv2_flat")
            self.print_log(h_conv2_flat)
    
            W_fc1, b_fc1 = self.make_layer_variables([conv_out_size, 256], trainable, "fc1")
            h_fc1 = tf.nn.relu(tf.matmul(h_conv2_flat, W_fc1) + b_fc1, name="h_fc1")
            self.print_log(h_fc1)
    
            W_fc2, b_fc2 = self.make_layer_variables([256, num_actions], trainable, "fc2")
            y = tf.matmul(h_fc1, W_fc2) + b_fc2
            self.print_log(y)
            
            y_class = tf.nn.softmax(y)
            
            W_fc3, b_fc3 = self.make_layer_variables([256, 1], trainable, "fc3")
            v_ = tf.matmul(h_fc1, W_fc3) + b_fc3
            v = tf.reshape(v_, [-1] )
        
        self.x = x_in
        self.y = y
        self.y_class = y_class
        self.v = v
        tvars = tf.trainable_variables()
        self.variables = [tvar for tvar in tvars if tvar.name.startswith(name)]
        print 'len(self.variables) : %s' % len(self.variables)

