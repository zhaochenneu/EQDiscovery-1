# =============================================================================
# Physics-informed learning of governing equations from scarce data
# Zhao Chen, Yang Liu, and Hao Sun
# 2021. Northeastern University

# Please run FN_Pre.py, FN_ADO.py and FN_Pt.py in order
# =============================================================================

import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt
import scipy.io
import time
from pyDOE import lhs
from tqdm import tqdm
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '0'  # limite all operations on this GPU

import matplotlib as mpl
mpl.rcParams['agg.path.chunksize'] = 10000

with tf.device('/device:GPU:0'): # '/device:GPU:0' is GPU1 in task manager. Likewise, '/device:GPU:1' is GPU0.
        
    # ADO loss history
    loss_u_history = np.array([0])
    loss_v_history = np.array([0])
    loss_f_u_history = np.array([0])
    loss_f_v_history = np.array([0])
    loss_bc_all_history = np.array([0])
    step = 0
        
    # STRidge loss history   
    loss_u_best_history_STRidge = np.array([0])
    loss_f_u_best_history_STRidge = np.array([0])
    loss_lambda_u_best_history_STRidge = np.array([0])
    tol_u_best_history_STRidge = np.array([0])
    lambda_normalized_u_best_history_STRidge = np.zeros((72,1))
  
    loss_v_best_history_STRidge = np.array([0])
    loss_f_v_best_history_STRidge = np.array([0])
    loss_lambda_v_best_history_STRidge = np.array([0])
    tol_v_best_history_STRidge = np.array([0])
    lambda_normalized_v_best_history_STRidge = np.zeros((72,1))

    lambda_u_history_STRidge = np.zeros((72, 1))
    ridge_u_append_counter_STRidge = np.array([0])

    lambda_v_history_STRidge = np.zeros((72, 1))
    ridge_v_append_counter_STRidge = np.array([0])
        
    np.random.seed(1234)
    tf.set_random_seed(1234)
    
    class PhysicsInformedNN:
# =============================================================================
#     Inspired by Raissi, Maziar, Paris Perdikaris, and George E. Karniadakis.
#     "Physics-informed neural networks: A deep learning framework for solving forward and inverse problems
#     involving nonlinear partial differential equations." Journal of Computational Physics 378 (2019): 686-707.
# =============================================================================

        # Initialize the class
        def __init__(self, X, U, X_f, layers_s, layers_i, lb, ub, X_l, X_r, X_u, X_b, X_bc_meas, U_bc_meas):
            self.lb = lb
            self.ub = ub
            
            # Initialize NNs
            self.weights_s, self.biases_s = self.initialize_NN(layers_s) # root NN
            self.weights0, self.biases0 = self.initialize_NN(layers_i) # branch NN1 
            self.weights1, self.biases1 = self.initialize_NN(layers_i) # branch NN2
            self.weights2, self.biases2 = self.initialize_NN(layers_i) # branch NN3

            config=tf.ConfigProto(allow_soft_placement=True, log_device_placement=True)
            config.gpu_options.allow_growth = True
            self.sess = tf.Session(config = config)
            
            # Initialize parameters            
            self.lambda_u_tf = tf.placeholder(tf.float32, shape=[72, 1])
            self.lambda_v_tf = tf.placeholder(tf.float32, shape=[72, 1])
            
            # create these to make sure I can restore pretrained parameters
            self.lambda_u = tf.Variable(tf.random_uniform([70, 1], minval = -1, maxval = 1, dtype=tf.float32))
            self.lambda_v = tf.Variable(tf.random_uniform([70, 1], minval = -1, maxval = 1, dtype=tf.float32))
            self.diff_coeff_u_core = tf.Variable(tf.random_uniform([], minval = -1, maxval = 1, dtype=tf.float32))
            self.diff_coeff_v_core = tf.Variable(tf.random_uniform([], minval = -1, maxval = 1, dtype=tf.float32))
            self.diff_u_scale = 5
            self.diff_v_scale = 150
            
            # Specify the list of trainable variables 
            self.var_list_1 = self.biases0 + self.weights0 + \
                self.biases2 + self.weights2 + \
                self.biases1 + self.weights1 + \
                self.weights_s + self.biases_s
            
            self.var_list_Pretrain = self.var_list_1 + [self.diff_coeff_u_core] + [self.diff_coeff_v_core]
            self.var_list_Pretrain.append(self.lambda_u)
            self.var_list_Pretrain.append(self.lambda_v)

            ######### Training data ################            
            self.X_tf = tf.placeholder(tf.float32)
            self.U_tf = tf.placeholder(tf.float32)

            self.x_f_tf = tf.placeholder(tf.float32, shape=[None, 1])
            self.y_f_tf = tf.placeholder(tf.float32, shape=[None, 1])
            self.t_f_tf = tf.placeholder(tf.float32, shape=[None, 1])

            self.U0_pred = self.net_U(self.X_tf, 0)
            self.U1_pred = self.net_U(self.X_tf, 1)
            self.U2_pred = self.net_U(self.X_tf, 2)

            self.f_u_pred, self.f_v_pred, self.Phi_pred, self.lhs_u, self.lhs_v = self.net_f(self.x_f_tf, self.y_f_tf, self.t_f_tf)

            self.loss_u = tf.reduce_mean(tf.square(self.U_tf[:, 0, 0] - self.U0_pred[:, 0])) + \
                tf.reduce_mean(tf.square(self.U_tf[:, 0, 1] - self.U1_pred[:, 0])) + \
                tf.reduce_mean(tf.square(self.U_tf[:, 0, 2] - self.U2_pred[:, 0]))

            self.loss_v = tf.reduce_mean(tf.square(self.U_tf[:, 1, 0] - self.U0_pred[:, 1])) + \
                tf.reduce_mean(tf.square(self.U_tf[:, 1, 1] - self.U1_pred[:, 1])) + \
                tf.reduce_mean(tf.square(self.U_tf[:, 1, 2] - self.U2_pred[:, 1]))

            self.loss_u_coeff = tf.placeholder(tf.float32)
            self.loss_v_coeff = tf.placeholder(tf.float32)
            
            self.loss_U = self.loss_u_coeff*self.loss_u + self.loss_v_coeff*self.loss_v
            
            self.loss_f_u = tf.reduce_mean(tf.square(self.f_u_pred))
            self.loss_f_v = tf.reduce_mean(tf.square(self.f_v_pred))
            
            self.loss_f_v_coeff = tf.placeholder(tf.float32)
            self.loss_f = 10*self.loss_f_u + self.loss_f_v_coeff*self.loss_f_v
            
            self.x_l_tf = tf.placeholder(tf.float32)
            self.x_r_tf = tf.placeholder(tf.float32)
            self.x_u_tf = tf.placeholder(tf.float32)
            self.x_b_tf = tf.placeholder(tf.float32)
            
            self.y_l_tf = tf.placeholder(tf.float32)
            self.y_r_tf = tf.placeholder(tf.float32)
            self.y_u_tf = tf.placeholder(tf.float32)
            self.y_b_tf = tf.placeholder(tf.float32)

            self.t_l_tf = tf.placeholder(tf.float32)
            self.t_r_tf = tf.placeholder(tf.float32)
            self.t_u_tf = tf.placeholder(tf.float32)
            self.t_b_tf = tf.placeholder(tf.float32)

            self.U_l_0 = self.net_U(tf.concat((self.x_l_tf, self.y_l_tf, self.t_l_tf), 1), 0)
            self.U_r_0 = self.net_U(tf.concat((self.x_r_tf, self.y_r_tf, self.t_r_tf), 1), 0)
            self.U_u_0 = self.net_U(tf.concat((self.x_u_tf, self.y_u_tf, self.t_u_tf), 1), 0)
            self.U_b_0 = self.net_U(tf.concat((self.x_b_tf, self.y_b_tf, self.t_b_tf), 1), 0)
            
            self.U_l_1 = self.net_U(tf.concat((self.x_l_tf, self.y_l_tf, self.t_l_tf), 1), 1)
            self.U_r_1 = self.net_U(tf.concat((self.x_r_tf, self.y_r_tf, self.t_r_tf), 1), 1)
            self.U_u_1 = self.net_U(tf.concat((self.x_u_tf, self.y_u_tf, self.t_u_tf), 1), 1)
            self.U_b_1 = self.net_U(tf.concat((self.x_b_tf, self.y_b_tf, self.t_b_tf), 1), 1)

            self.U_l_2 = self.net_U(tf.concat((self.x_l_tf, self.y_l_tf, self.t_l_tf), 1), 2)
            self.U_r_2 = self.net_U(tf.concat((self.x_r_tf, self.y_r_tf, self.t_r_tf), 1), 2)
            self.U_u_2 = self.net_U(tf.concat((self.x_u_tf, self.y_u_tf, self.t_u_tf), 1), 2)
            self.U_b_2 = self.net_U(tf.concat((self.x_b_tf, self.y_b_tf, self.t_b_tf), 1), 2)
            
            self.loss_bc = tf.reduce_mean(tf.square(self.U_l_0 - self.U_r_0)) + tf.reduce_mean(tf.square(self.U_u_0 - self.U_b_0)) + \
                tf.reduce_mean(tf.square(self.U_l_1 - self.U_r_1)) + tf.reduce_mean(tf.square(self.U_u_1 - self.U_b_1)) + \
                tf.reduce_mean(tf.square(self.U_l_2 - self.U_r_2)) + tf.reduce_mean(tf.square(self.U_u_2 - self.U_b_2))
            
            # bc meas loss
            self.X_bc_meas_tf = tf.placeholder(tf.float32)
            self.U_bc_meas_tf = tf.placeholder(tf.float32)

            self.U0_bc_pred = self.net_U(self.X_bc_meas_tf, 0)
            self.U1_bc_pred = self.net_U(self.X_bc_meas_tf, 1)
            self.U2_bc_pred = self.net_U(self.X_bc_meas_tf, 2)

            self.loss_bc_meas = tf.reduce_mean(tf.square(self.U_bc_meas_tf[:, 0, 0] - self.U0_bc_pred[:, 0])) + \
                tf.reduce_mean(tf.square(self.U_bc_meas_tf[:, 1, 0] - self.U0_bc_pred[:, 1])) + \
                tf.reduce_mean(tf.square(self.U_bc_meas_tf[:, 0, 1] - self.U1_bc_pred[:, 0])) + \
                tf.reduce_mean(tf.square(self.U_bc_meas_tf[:, 1, 1] - self.U1_bc_pred[:, 1])) + \
                tf.reduce_mean(tf.square(self.U_bc_meas_tf[:, 0, 2] - self.U2_bc_pred[:, 0])) + \
                tf.reduce_mean(tf.square(self.U_bc_meas_tf[:, 1, 2] - self.U2_bc_pred[:, 1]))
                
            self.loss_bc_all = self.loss_bc + self.loss_bc_meas

            self.loss_ADO = tf.log(self.loss_U + self.loss_f + self.loss_bc_all)
                        
# =============================================================================
# Optimizers
# =============================================================================
            # ADO opt
            self.global_step = tf.Variable(0, trainable=False)
            starter_learning_rate = 1e-4
            self.learning_rate = tf.train.exponential_decay(starter_learning_rate, self.global_step, 10000, 0.8,
                                                         staircase=True)

            # The default settings: learning rate = 1e-3, beta1 = 0.9, beta2 = 0.999， epsilon = 1e-8
            self.optimizer_Adam = tf.train.AdamOptimizer(learning_rate = self.learning_rate) 
            self.train_op_Adam = self.optimizer_Adam.minimize(self.loss_ADO, var_list = self.var_list_1, 
                                                              global_step = self.global_step)

            self.optimizer = tf.contrib.opt.ScipyOptimizerInterface(self.loss_ADO, var_list = self.var_list_1,
    #                                                                    L-BFGS-B
                                                                    method = 'L-BFGS-B', 
                                                                    options = {'maxiter': 10000, #10000
                                                                               'maxfun': 10000, 
                                                                               'maxcor': 50,
                                                                               'maxls': 50,
                                                                               'ftol' : 1.0 * np.finfo(float).eps})
            
            self.tf_dict = {self.X_tf: X,  self.U_tf: U, self.x_f_tf: X_f[:, 0:1], self.y_f_tf: X_f[:, 1:2], self.t_f_tf: X_f[:, 2:3],
                            self.x_l_tf: X_l[:, :1], self.x_r_tf: X_r[:, :1], self.x_u_tf: X_u[:, :1], self.x_b_tf: X_b[:, :1],
                            self.y_l_tf: X_l[:, 1:2], self.y_r_tf: X_r[:, 1:2], self.y_u_tf: X_u[:, 1:2], self.y_b_tf: X_b[:, 1:2],
                            self.t_l_tf: X_l[:, 2:3], self.t_r_tf: X_r[:, 2:3], self.t_u_tf: X_u[:, 2:3], self.t_b_tf: X_b[:, 2:3],
                            self.X_bc_meas_tf: X_bc_meas, self.U_bc_meas_tf: U_bc_meas}
            
            init = tf.global_variables_initializer()
            self.sess.run(init)
                
        def initialize_NN(self, layers):        
            weights = []
            biases = []
            num_layers = len(layers) 
            for l in range(0,num_layers-1):
                W = self.xavier_init(size=[layers[l], layers[l+1]])
                b = tf.Variable(tf.zeros([1,layers[l+1]], dtype=tf.float32), dtype=tf.float32, name = 'b')
                weights.append(W)
                biases.append(b)        
                
            return weights, biases
            
        def xavier_init(self, size):
            in_dim = size[0]
            out_dim = size[1]        
            xavier_stddev = np.sqrt(2/(in_dim + out_dim))
            return tf.Variable(tf.truncated_normal([in_dim, out_dim], stddev=xavier_stddev, dtype=tf.float32),
                                dtype=tf.float32, name = 'W')
 
        def neural_net(self, X, weights, biases, si_flag):
            num_layers = len(weights) + 1    
            n = 1 # scaling factor
            if si_flag: # root NN
                H = 2.0*(X - self.lb)/(self.ub - self.lb) - 1.0
                W = weights[0]
                b = biases[0]
                H = tf.tanh(tf.add(tf.matmul(H, W), b))
            else: # branch NN
                H = X
                W = weights[0]
                b = biases[0]
                H = tf.tanh(tf.add(tf.matmul(H, W), b))

            for l in range(1, num_layers-2):
                W = weights[l]
                b = biases[l]
                H = tf.tanh(n*tf.add(tf.matmul(H, W), b))
            W = weights[-1]
            b = biases[-1] 
            if si_flag: # root NN
                Y = tf.tanh(n*tf.add(tf.matmul(H, W), b))
            else: # branch NN
                Y = tf.add(tf.matmul(H, W), b)
            return Y
        
        def net_U(self, X, IBC_flag):  
            U_int = self.neural_net(X, self.weights_s, self.biases_s, True) # root NN
            if IBC_flag == 0: # branch NN 1
                U = self.neural_net(U_int, self.weights0, self.biases0, False)
            elif IBC_flag == 1: # branch NN 2
                U = self.neural_net(U_int, self.weights1, self.biases1, False)
            elif IBC_flag == 2: # branch NN 3
                U = self.neural_net(U_int, self.weights2, self.biases2, False)
            return U
                       
        def net_f(self, x, y, t):
            U0 = self.net_U(tf.concat((x, y, t), 1), 0)
            U1 = self.net_U(tf.concat((x, y, t), 1), 1)
            U2 = self.net_U(tf.concat((x, y, t), 1), 2)

            u0 = U0[:, 0:1]
            v0 = U0[:, 1:2]
            u0_t = tf.gradients(u0, t)[0]
            u0_x = tf.gradients(u0, x)[0]
            u0_xx = tf.gradients(u0_x, x)[0]
            u0_xy = tf.gradients(u0_x, y)[0]
            u0_y = tf.gradients(u0, y)[0]
            u0_yy = tf.gradients(u0_y, y)[0]
            v0_t = tf.gradients(v0, t)[0]
            v0_x = tf.gradients(v0, x)[0]
            v0_xx = tf.gradients(v0_x, x)[0]
            v0_xy = tf.gradients(v0_x, y)[0]
            v0_y = tf.gradients(v0, y)[0]
            v0_yy = tf.gradients(v0_y, y)[0]

            u1 = U1[:, 0:1]
            v1 = U1[:, 1:2]
            u1_t = tf.gradients(u1, t)[0]
            u1_x = tf.gradients(u1, x)[0]
            u1_xx = tf.gradients(u1_x, x)[0]
            u1_xy = tf.gradients(u1_x, y)[0]
            u1_y = tf.gradients(u1, y)[0]
            u1_yy = tf.gradients(u1_y, y)[0]
            v1_t = tf.gradients(v1, t)[0]
            v1_x = tf.gradients(v1, x)[0]
            v1_xy = tf.gradients(v1_x, y)[0]
            v1_xx = tf.gradients(v1_x, x)[0]
            v1_y = tf.gradients(v1, y)[0]
            v1_yy = tf.gradients(v1_y, y)[0]

            u2 = U2[:, 0:1]
            v2 = U2[:, 1:2]
            u2_t = tf.gradients(u2, t)[0]
            u2_x = tf.gradients(u2, x)[0]
            u2_xx = tf.gradients(u2_x, x)[0]
            u2_xy = tf.gradients(u2_x, y)[0]
            u2_y = tf.gradients(u2, y)[0]
            u2_yy = tf.gradients(u2_y, y)[0]
            v2_t = tf.gradients(v2, t)[0]
            v2_x = tf.gradients(v2, x)[0]
            v2_xx = tf.gradients(v2_x, x)[0]
            v2_xy = tf.gradients(v2_x, y)[0]
            v2_y = tf.gradients(v2, y)[0]
            v2_yy = tf.gradients(v2_y, y)[0]

            # 3 ICs
            u_t = tf.concat((u0_t, u1_t, u2_t), 0)
            v_t = tf.concat((v0_t, v1_t, v2_t), 0)
            
            u_x = tf.concat((u0_x, u1_x, u2_x), 0)
            u_xx = tf.concat((u0_xx, u1_xx, u2_xx), 0)
            u_y = tf.concat((u0_y, u1_y, u2_y), 0)
            u_yy = tf.concat((u0_yy, u1_yy, u2_yy), 0)
            u_xy = tf.concat((u0_xy, u1_xy, u2_xy), 0)
            
            v_x = tf.concat((v0_x, v1_x, v2_x), 0)
            v_xx = tf.concat((v0_xx, v1_xx, v2_xx), 0)
            v_xy = tf.concat((v0_xy, v1_xy, v2_xy), 0)
            v_y = tf.concat((v0_y, v1_y, v2_y), 0)
            v_yy = tf.concat((v0_yy, v1_yy, v2_yy), 0)

            data = [tf.concat((u0, u1, u2), 0), tf.concat((v0, v1, v2), 0)]            
            
            # build a polynomial&derivative library
            derivatives = [tf.ones_like(data[0], optimize = False, dtype = tf.float32)]
            derivatives.append(u_x)
            derivatives.append(u_y)
            derivatives.append(u_xy)
            derivatives.append(v_x)
            derivatives.append(v_y)
            derivatives.append(v_xy)

            derivatives_description = ['', 'u_{x}', 'u_{y}', 'u_{xy}','v_{x}', 'v_{y}','v_{xy}']
            
            Phi, self.lib_descr = self.build_PolyDeriLibrary(data, derivatives, derivatives_description, PolyOrder = 3, 
                                                    data_description = ['u','v'])      
            Phi = tf.concat((u_xx + u_yy, v_xx + v_yy, Phi), axis = 1)
            self.lib_descr = ['(u_xx + u_yy)', '(v_xx + v_yy)'] + self.lib_descr
            f_u = tf.matmul(Phi, self.lambda_u_tf) - u_t      
            f_v = tf.matmul(Phi, self.lambda_v_tf) - v_t
                        
            return f_u, f_v, Phi, u_t, v_t

        def build_PolyDeriLibrary(self, data, lib_deri, lib_deri_descr, PolyOrder, data_description = None):         
            ## polynomial terms
            P = PolyOrder
            lib_poly = [tf.ones_like(data[0], optimize = False, dtype = tf.float32)]
            lib_poly_descr = [''] # it denotes '1'
            for i in range(len(data)): # polynomial terms of univariable
                for j in range(1, P+1):
                    lib_poly.append((data[i])**j)
                    lib_poly_descr.append(data_description[i]+"**"+str(j))
                    
            for i in range(1,P): # polynomial terms of bivariable. Assume we only have 2 variables.
                for j in range(1,P-i+1):
                    lib_poly.append((data[0])**i*(data[1])**j)
                    lib_poly_descr.append(data_description[0]+"**"+str(i)+data_description[1]+"**"+str(j))
                    
            ## derivative terms            
            ## Multiplication of derivatives and polynomials (including the multiplication with '1')
            lib_poly_deri = []
            lib_poly_deri_descr = []
            for i in range(len(lib_poly)):
                for j in range(len(lib_deri)):
                    lib_poly_deri.append(lib_poly[i]*lib_deri[j])
                    lib_poly_deri_descr.append(lib_poly_descr[i]+lib_deri_descr[j])

            return tf.concat(lib_poly_deri, axis = 1), lib_poly_deri_descr
        
        def callback(self, loss_u, loss_v, loss_f_u, loss_f_v, loss_bc_all):
            
            global step
            if step%10 == 0:
                global loss_u_history
                global loss_v_history
                global loss_f_u_history
                global loss_f_v_history
                global loss_bc_all_history
                global loss_bc_meas_history
                
                loss_u_history = np.append(loss_u_history, loss_u)
                loss_v_history = np.append(loss_v_history, loss_v)
                loss_f_u_history = np.append(loss_f_u_history, loss_f_u)
                loss_f_v_history = np.append(loss_f_v_history, loss_f_v)
                loss_bc_all_history = np.append(loss_bc_all_history, loss_bc_all)
            step = step+1

        def train(self): 
        
            self.tf_dict[self.loss_u_coeff] = 1
            self.tf_dict[self.loss_v_coeff] = 1
            self.tf_dict[self.loss_f_v_coeff] = 10
            self.anneal_lam = [1, 1, 1]
            self.anneal_alpha = 0.8

            saver = tf.train.Saver(self.var_list_Pretrain)                        
            saver.restore(self.sess, './saved_variable_Pre')

            # initialize STRidge tol, then inherit it previous ADO epochs
            self.MyTol_u = 0
            self.MyTol_v = 0
            
            # pass pretrained lambda and diffu coeff to new lambda_tf
            lambda_u, diff_coeff_u_core = self.sess.run([self.lambda_u, self.diff_coeff_u_core])
            lambda_v, diff_coeff_v_core = self.sess.run([self.lambda_v, self.diff_coeff_v_core])
            diff_coeff_u = np.reshape(self.diff_u_scale/(1 + np.exp(-diff_coeff_u_core)), (1, 1))
            diff_coeff_v = np.reshape(self.diff_v_scale/(1 + np.exp(-diff_coeff_v_core)), (1, 1))
            self.tf_dict[self.lambda_u_tf] = np.concatenate((diff_coeff_u, diff_coeff_v, lambda_u), axis = 0)
            self.tf_dict[self.lambda_v_tf] = np.concatenate((diff_coeff_u, diff_coeff_v, lambda_v), axis = 0)

            for it in tqdm(range(10)):     
                # Loop of STRidge estimation
                print('STRidge begins')
                self.callTrainSTRidge()

                # Loop of Adam optimization
                print('Adam begins')
                for it_Adam in tqdm(range(10000)):    # 10000
                    self.sess.run(self.train_op_Adam, self.tf_dict)                    
                    # Print
                    if it_Adam % 10 == 0:
                        loss_u, loss_v, loss_f_u, loss_f_v = self.sess.run([self.loss_u, self.loss_v,
                                                                                 self.loss_f_u,
                                                                                 self.loss_f_v], 
                                                                                self.tf_dict)
                        loss_bc_all = self.sess.run(self.loss_bc_all, self.tf_dict)

                        global loss_u_history
                        global loss_v_history
                        global loss_f_u_history
                        global loss_f_v_history
                        global loss_bc_all_history
                        global loss_bc_meas_history
                        
                        loss_u_history = np.append(loss_u_history, loss_u)
                        loss_v_history = np.append(loss_v_history, loss_v)
                        loss_f_u_history = np.append(loss_f_u_history, loss_f_u)
                        loss_f_v_history = np.append(loss_f_v_history, loss_f_v)
                        loss_bc_all_history = np.append(loss_bc_all_history, loss_bc_all)
                        
                # Loop of L-BFGS-B optimization
                print('L-BFGS-B begins')
                self.optimizer.minimize(self.sess,
                                        feed_dict = self.tf_dict,
                                        fetches = [self.loss_u, self.loss_v, self.loss_f_u, self.loss_f_v,
                                                    self.loss_bc_all],
                                        loss_callback = self.callback)   
                                
            saver_ADO = tf.train.Saver(self.var_list_1)                        
            saved_path = saver_ADO.save(self.sess, './saved_variable_ADO')

        def callTrainSTRidge(self):
            lam = 1e-6
            d_tol = 1
            maxit = 50
            STR_iters = 10
            l0_penalty = 5e-4
            normalize = 2
            split = 1
            Phi_pred, lhs_u, lhs_v = self.sess.run([self.Phi_pred, self.lhs_u, self.lhs_v], self.tf_dict) 
            
            lambda_u = self.TrainSTRidge(Phi_pred, lhs_u, lam, d_tol, maxit, STR_iters, l0_penalty, normalize, split,
                                              uv_Flag = 'u')     
            self.tf_dict[self.lambda_u_tf] = lambda_u
            
            lambda_v = self.TrainSTRidge(Phi_pred, lhs_v, lam, d_tol, maxit, STR_iters, l0_penalty, normalize, split,
                                              uv_Flag = 'v')     
            self.tf_dict[self.lambda_v_tf] = lambda_v
            
        def TrainSTRidge(self, R0, Ut, lam, d_tol, maxit, STR_iters = 10, l0_penalty = None, normalize = 2, split = 0.8, 
                          uv_Flag = 'u'):               
# =============================================================================
#        Inspired by Rudy, Samuel H., et al. "Data-driven discovery of partial differential equations."
#        Science Advances 3.4 (2017): e1602614.
# =============================================================================      
            # Find normalization coeff s.t. we can record the normalized lambda
            n,d = R0.shape
            if normalize != 0:
                Mreg = np.zeros((d,1))
                for i in range(0,d):
                    Mreg[i] = 1.0/(np.linalg.norm(R0[:,i],normalize))
                                            
            # keep the normalization coeff of the constant term as zeros
            if uv_Flag == 'u':
                self.ConstScaleFac = 1e-5
            elif uv_Flag == 'v':
                self.ConstScaleFac = 5e-4
            Mreg[2] = self.ConstScaleFac
            
            # Set up the initial tolerance and l0 penalty
            d_tol = float(d_tol)
            if uv_Flag == 'u':
                tol = self.MyTol_u
            elif uv_Flag == 'v':
                tol = self.MyTol_v
                
            if uv_Flag == 'u':
                w_best = self.tf_dict[self.lambda_u_tf]
            else:
                w_best = self.tf_dict[self.lambda_v_tf]
            
            err_f = np.mean((Ut - R0.dot(w_best))**2)
            err_lambda = l0_penalty*np.count_nonzero(w_best)
            err_best = err_f + err_lambda
            
            if uv_Flag == 'u':
                tol_best = self.MyTol_u
            elif uv_Flag == 'v':
                tol_best = self.MyTol_v
                        
            if uv_Flag == 'u':                
                global loss_u_best_history_STRidge 
                global loss_f_u_best_history_STRidge 
                global loss_lambda_u_best_history_STRidge 
                global tol_u_best_history_STRidge 
                global lambda_normalized_u_best_history_STRidge                 
                loss_u_best_history_STRidge = np.append(loss_u_best_history_STRidge, err_best)
                loss_f_u_best_history_STRidge = np.append(loss_f_u_best_history_STRidge, err_f)
                loss_lambda_u_best_history_STRidge = np.append(loss_lambda_u_best_history_STRidge, err_lambda)
                tol_u_best_history_STRidge = np.append(tol_u_best_history_STRidge, tol_best)
                lambda_normalized_u_best_history_STRidge = np.append(lambda_normalized_u_best_history_STRidge,
                                                                np.reshape(w_best/Mreg, (-1, 1)), axis = 1)
            else:                
                global loss_v_best_history_STRidge 
                global loss_f_v_best_history_STRidge 
                global loss_lambda_v_best_history_STRidge 
                global tol_v_best_history_STRidge 
                global lambda_normalized_v_best_history_STRidge                 
                loss_v_best_history_STRidge = np.append(loss_v_best_history_STRidge, err_best)
                loss_f_v_best_history_STRidge = np.append(loss_f_v_best_history_STRidge, err_f)
                loss_lambda_v_best_history_STRidge = np.append(loss_lambda_v_best_history_STRidge, err_lambda)
                tol_v_best_history_STRidge = np.append(tol_v_best_history_STRidge, tol_best)
                lambda_normalized_v_best_history_STRidge = np.append(lambda_normalized_v_best_history_STRidge,
                                                                np.reshape(w_best/Mreg, (-1, 1)), axis = 1)
        
            # Now increase tolerance until test performance decreases
            for iter in range(maxit):
                # Get a set of coefficients and error
                w = self.STRidge(R0, Ut, lam, STR_iters, tol, normalize, uv_Flag = uv_Flag)
                err_f = np.mean((Ut - R0.dot(w))**2)
                err_lambda = l0_penalty*np.count_nonzero(w)
                err = err_f + err_lambda
        
                # Has the accuracy improved?
                if err <= err_best:
                    err_best = err
                    w_best = w
                    tol_best = tol
                    tol = tol + d_tol
                    
                    if uv_Flag == 'u':
                        loss_u_best_history_STRidge = np.append(loss_u_best_history_STRidge , err_best)
                        loss_f_u_best_history_STRidge = np.append(loss_f_u_best_history_STRidge , err_f)
                        loss_lambda_u_best_history_STRidge = np.append(loss_lambda_u_best_history_STRidge,
                                                                        err_lambda)
                        tol_u_best_history_STRidge = np.append(tol_u_best_history_STRidge, tol_best)
                        lambda_normalized_u_best_history_STRidge = np.append(lambda_normalized_u_best_history_STRidge,
                                                                        np.reshape(w_best/Mreg, (-1, 1)), axis = 1)
                    else:
                        loss_v_best_history_STRidge = np.append(loss_v_best_history_STRidge , err_best)
                        loss_f_v_best_history_STRidge = np.append(loss_f_v_best_history_STRidge , err_f)
                        loss_lambda_v_best_history_STRidge = np.append(loss_lambda_v_best_history_STRidge,
                                                                        err_lambda)
                        tol_v_best_history_STRidge = np.append(tol_v_best_history_STRidge, tol_best)
                        lambda_normalized_v_best_history_STRidge = np.append(lambda_normalized_v_best_history_STRidge,
                                                                        np.reshape(w_best/Mreg, (-1, 1)), axis = 1)
                else:
                    tol = max([0,tol - 2*d_tol])
                    d_tol  = 2*d_tol / (maxit - iter)
                    tol = tol + d_tol
                                     
            if uv_Flag == 'u':
                self.MyTol_u = tol_best
            elif uv_Flag == 'v':
                self.MyTol_v = tol_best
            
            return np.real(w_best)     
        
        def STRidge(self, X0, y, lam, maxit, tol, normalize, print_results = False, uv_Flag = 'u'):
            
            # First normalize data 
            n,d = X0.shape
            X = np.zeros((n,d), dtype=np.float32)
            Mreg = np.zeros((d,1))
            for i in range(0,d):
                Mreg[i] = 1.0/(np.linalg.norm(X0[:,i], normalize))
                X[:,i] = Mreg[i]*X0[:,i]               
            
            # delibrately downscale the constant term in the normalized library
            X[:, 2] = np.ones_like(X[:, 2])*self.ConstScaleFac
            Mreg[2] = self.ConstScaleFac

            if uv_Flag == 'u':
                w = self.tf_dict[self.lambda_u_tf]/Mreg
            else:
                w = self.tf_dict[self.lambda_v_tf]/Mreg            

            num_relevant = d
            
            # norm threshold
            biginds = np.where(abs(w[2:]) > tol)[0] + 2 # keep diffu terms unpruned
            if uv_Flag == 'u':
                biginds = np.insert(biginds, obj = 0, values = 0) # keep diffu u term unpruned
            else:
                biginds = np.insert(biginds, obj = 0, values = 1) # keep diffu v term unpruned

            if uv_Flag == 'u':
                global ridge_u_append_counter_STRidge
                ridge_u_append_counter = 0
                
                global lambda_u_history_STRidge
                lambda_u_history_STRidge = np.append(lambda_u_history_STRidge, np.multiply(Mreg,w), axis = 1)
                ridge_u_append_counter += 1
            else:
                global ridge_v_append_counter_STRidge
                ridge_v_append_counter = 0
                
                global lambda_v_history_STRidge
                lambda_v_history_STRidge = np.append(lambda_v_history_STRidge, np.multiply(Mreg,w), axis = 1)
                ridge_v_append_counter += 1

            # Threshold and continue
            for j in range(maxit):
        
                # Figure out which items to cut out
                
                # norm threshold
                smallinds = np.where(abs(w[2:]) < tol)[0] + 2 # dont threhold diffu terms 
                if uv_Flag == 'u':
                    smallinds = np.insert(smallinds, obj = 0, values = 1) # prune diffu v term
                else:
                    smallinds = np.insert(smallinds, obj = 0, values = 0) # prune diffu u term
                
                new_biginds = [i for i in range(d) if i not in smallinds]
                    
                # If nothing changes then stop
                if num_relevant == len(new_biginds): break
                else: num_relevant = len(new_biginds)
                    
                # Also make sure we didn't just lose all the coefficients
                if len(new_biginds) == 0:
                    if j == 0: 
                        if uv_Flag == 'u':
                            lambda_u_history_STRidge = np.append(lambda_u_history_STRidge, w*Mreg, axis = 1)
                            ridge_u_append_counter += 1
                            ridge_u_append_counter_STRidge = np.append(ridge_u_append_counter_STRidge, ridge_u_append_counter)
                        else:
                            lambda_v_history_STRidge = np.append(lambda_v_history_STRidge, w*Mreg, axis = 1)
                            ridge_v_append_counter += 1
                            ridge_v_append_counter_STRidge = np.append(ridge_v_append_counter_STRidge, ridge_v_append_counter)
                        return w*Mreg
                    else: break
                
                biginds = new_biginds
                
                # Otherwise get a new guess
                w[smallinds] = 0
                
                if lam != 0: 
                    w[biginds] = np.linalg.lstsq(X[:, biginds].T.dot(X[:, biginds]) + \
                                                          lam*np.eye(len(biginds)), X[:, biginds].T.dot(y))[0]
                    if uv_Flag == 'u':
                        lambda_u_history_STRidge = np.append(lambda_u_history_STRidge, np.multiply(Mreg,w), axis = 1)
                        ridge_u_append_counter += 1
                    else:
                        lambda_v_history_STRidge = np.append(lambda_v_history_STRidge, np.multiply(Mreg,w), axis = 1)
                        ridge_v_append_counter += 1
                else: 
                    w[biginds] = np.linalg.lstsq(X[:, biginds],y)[0]
                    if uv_Flag == 'u':
                        lambda_u_history_STRidge = np.append(lambda_u_history_STRidge, np.multiply(Mreg,w), axis = 1)
                        ridge_u_append_counter += 1
                    else:
                        lambda_v_history_STRidge = np.append(lambda_v_history_STRidge, np.multiply(Mreg,w), axis = 1)
                        ridge_v_append_counter += 1
            # Now that we have the sparsity pattern, use standard least squares to get w
            if biginds != []: w[biginds] = np.linalg.lstsq(X[:, biginds],y)[0]
            
            if uv_Flag == 'u':
                lambda_u_history_STRidge = np.append(lambda_u_history_STRidge, w*Mreg, axis = 1)
                ridge_u_append_counter += 1
                ridge_u_append_counter_STRidge = np.append(ridge_u_append_counter_STRidge, ridge_u_append_counter)
            else:
                lambda_v_history_STRidge = np.append(lambda_v_history_STRidge, w*Mreg, axis = 1)
                ridge_v_append_counter += 1
                ridge_v_append_counter_STRidge = np.append(ridge_v_append_counter_STRidge, ridge_v_append_counter)

            return w*Mreg
        
        def predict(self, X_star):            
            tf_dict = {self.X_tf: X_star}            
            U0 = self.sess.run(self.U0_pred, tf_dict)
            U1 = self.sess.run(self.U1_pred, tf_dict)
            U2 = self.sess.run(self.U2_pred, tf_dict)
            return np.stack((U0, U1, U2), axis = -1)
                
    def DownsampleMeas(idx_x, idx_t, xx, yy, tt, Exact_u_IC1, Exact_v_IC1, Exact_u_IC2 = 0, Exact_v_IC2 = 0, Exact_u_IC3 = 0, Exact_v_IC3 = 0, XU_flag = False):
        xx0 = xx[:, :, idx_t]
        xx1 = xx0[idx_x, :, :]
        xx2 = xx1[:, idx_x, :]
        
        yy0 = yy[:, :, idx_t]
        yy1 = yy0[idx_x, :, :]
        yy2 = yy1[:, idx_x, :]
        
        tt0 = tt[:, :, idx_t]
        tt1 = tt0[idx_x, :, :]
        tt2 = tt1[:, idx_x, :]    
        
        X_U_meas = np.hstack((xx2.flatten()[:,None], yy2.flatten()[:,None], tt2.flatten()[:,None]))  

        # IC1
        Exact_u_IC1_0 = Exact_u_IC1[:, :, idx_t]
        Exact_u_IC1_1 = Exact_u_IC1_0[idx_x, :, :]
        Exact_u_IC1_2 = Exact_u_IC1_1[:, idx_x, :]
        Exact_v_IC1_0 = Exact_v_IC1[:, :, idx_t]
        Exact_v_IC1_1 = Exact_v_IC1_0[idx_x, :, :]
        Exact_v_IC1_2 = Exact_v_IC1_1[:, idx_x, :]
        
        U_meas_IC1 = np.hstack((Exact_u_IC1_2.flatten()[:,None], Exact_v_IC1_2.flatten()[:,None]))  
        
        # IC2
        if type(Exact_u_IC2) != int:
            Exact_u_IC2_0 = Exact_u_IC2[:, :, idx_t]
            Exact_u_IC2_1 = Exact_u_IC2_0[idx_x, :, :]
            Exact_u_IC2_2 = Exact_u_IC2_1[:, idx_x, :]
            Exact_v_IC2_0 = Exact_v_IC2[:, :, idx_t]
            Exact_v_IC2_1 = Exact_v_IC2_0[idx_x, :, :]
            Exact_v_IC2_2 = Exact_v_IC2_1[:, idx_x, :]
            
            U_meas_IC2 = np.hstack((Exact_u_IC2_2.flatten()[:,None], Exact_v_IC2_2.flatten()[:,None]))  

        # IC3
        if type(Exact_u_IC3) != int:
            Exact_u_IC3_0 = Exact_u_IC3[:, :, idx_t]
            Exact_u_IC3_1 = Exact_u_IC3_0[idx_x, :, :]
            Exact_u_IC3_2 = Exact_u_IC3_1[:, idx_x, :]
            Exact_v_IC3_0 = Exact_v_IC3[:, :, idx_t]
            Exact_v_IC3_1 = Exact_v_IC3_0[idx_x, :, :]
            Exact_v_IC3_2 = Exact_v_IC3_1[:, idx_x, :]
        
            U_meas_IC3 = np.hstack((Exact_u_IC3_2.flatten()[:,None], Exact_v_IC3_2.flatten()[:,None]))  

        if type(Exact_u_IC2) != int and type(Exact_u_IC3) != int:
            U_meas = np.stack((U_meas_IC1, U_meas_IC2, U_meas_IC3), axis = -1)
        elif type(Exact_u_IC2) != int and type(Exact_u_IC3) == int:
            U_meas = np.stack((U_meas_IC1, U_meas_IC2), axis = -1)
        elif type(Exact_u_IC2) == int and type(Exact_u_IC3) == int:
            U_meas = U_meas_IC1

        if XU_flag:
            return X_U_meas, U_meas
        elif type(Exact_u_IC2) != int and type(Exact_u_IC3) != int:
            return xx2, yy2, tt2, Exact_u_IC1_2, Exact_v_IC1_2, Exact_u_IC2_2, Exact_v_IC2_2, Exact_u_IC3_2, Exact_v_IC3_2
        elif type(Exact_u_IC2) != int and type(Exact_u_IC3) == int:
            return xx2, yy2, tt2, Exact_u_IC1_2, Exact_v_IC1_2, Exact_u_IC2_2, Exact_v_IC2_2
        elif type(Exact_u_IC2) == int and type(Exact_u_IC3) == int:
            return xx2, yy2, tt2, Exact_u_IC1_2, Exact_v_IC1_2

    if __name__ == "__main__":              
        start_time = time.time()        
        layers_s = [3] + 2*[60] + [60]
        layers_i = 3*[60] + [2]

# =============================================================================
#       load data
# =============================================================================
        data_IC1 = scipy.io.loadmat('FN_IC2_Avoid104TS.mat')
        Exact_u_IC1 = np.real(data_IC1['u'])
        Exact_v_IC1 = np.real(data_IC1['v'])
        t = np.real(data_IC1['t'].flatten()[:,None])
        x = np.real(data_IC1['x'].flatten()[:,None])
        y = np.real(data_IC1['y'].flatten()[:,None])

        data_IC2 = scipy.io.loadmat('FN_IC3_Avoid104TS.mat')
        Exact_u_IC2 = np.real(data_IC2['u'])
        Exact_v_IC2 = np.real(data_IC2['v'])

        data_IC3 = scipy.io.loadmat('FN_IC4_Avoid104TS.mat')
        Exact_u_IC3 = np.real(data_IC3['u'])
        Exact_v_IC3 = np.real(data_IC3['v'])

        xx, yy, tt = np.meshgrid(x, y, t)
        
        X_star = np.hstack((xx.flatten()[:,None], yy.flatten()[:,None], tt.flatten()[:,None]))
        U_star_IC1 = np.hstack((Exact_u_IC1.flatten()[:,None], Exact_v_IC1.flatten()[:,None]))            
        U_star_IC2 = np.hstack((Exact_u_IC2.flatten()[:,None], Exact_v_IC2.flatten()[:,None]))            
        U_star_IC3 = np.hstack((Exact_u_IC3.flatten()[:,None], Exact_v_IC3.flatten()[:,None]))    
        U_star = np.stack((U_star_IC1, U_star_IC2, U_star_IC3), axis = -1)

        # Doman bounds
        lb = X_star.min(0)
        ub = X_star.max(0)    
            
        # Measurement data
        N_U_t = 31
        
        # continuous snapshots
        d_t = np.floor((t.shape[0])/(N_U_t - 1))
        idx_t = (np.arange(N_U_t)*d_t).astype(int)

        # downsize measurement grid
        N_U_x = 31
        d_x = np.floor((x.shape[0])/(N_U_x - 1))
        idx_x = (np.arange(1, N_U_x - 1)*d_x).astype(int) # remove bc
        X_U_meas, U_meas = DownsampleMeas(idx_x, idx_t, xx, yy, tt, Exact_u_IC1, Exact_v_IC1, Exact_u_IC2, Exact_v_IC2, Exact_u_IC3, Exact_v_IC3, XU_flag = True)

        # Training measurements, which are randomly sampled spatio-temporally
        Split_TrainVal = 1
        N_u_train = int(X_U_meas.shape[0]*Split_TrainVal)
        idx_train = np.random.choice(X_U_meas.shape[0], N_u_train, replace = False)
        np.random.shuffle(idx_train)
        X_U_train = X_U_meas[idx_train,:]
        U_train = U_meas[idx_train,:]
                                
        # bc meas
        idx_x = np.append((np.arange(N_U_x)*d_x).astype(int), -1) # include bc
        
        xx2, yy2, tt2, Exact_u_IC1_2, Exact_v_IC1_2, Exact_u_IC2_2, Exact_v_IC2_2, Exact_u_IC3_2, Exact_v_IC3_2 = DownsampleMeas(idx_x, idx_t, xx, yy, tt, Exact_u_IC1, Exact_v_IC1, Exact_u_IC2, Exact_v_IC2, Exact_u_IC3, Exact_v_IC3, XU_flag = False)

        # select the BCs                                                                                                                                 
        idx_x = np.array((0, -1)) # select the BCs
        xx_UB = xx2[idx_x, :, :]
        xx_LR = xx2[:, idx_x, :]
        
        yy_UB = yy2[idx_x, :, :]
        yy_LR = yy2[:, idx_x, :]

        tt_UB = tt2[idx_x, :, :]
        tt_LR = tt2[:, idx_x, :]    

        X_bc_meas_UB = np.hstack((xx_UB.flatten()[:,None], yy_UB.flatten()[:,None], tt_UB.flatten()[:,None]))  
        X_bc_meas_LR = np.hstack((xx_LR.flatten()[:,None], yy_LR.flatten()[:,None], tt_LR.flatten()[:,None]))  
        X_bc_meas = np.vstack((X_bc_meas_UB, X_bc_meas_LR))
        
        Exact_u_IC1_UB = Exact_u_IC1_2[idx_x, :, :]
        Exact_u_IC1_LR = Exact_u_IC1_2[:, idx_x, :]
        Exact_v_IC1_UB = Exact_v_IC1_2[idx_x, :, :]
        Exact_v_IC1_LR = Exact_v_IC1_2[:, idx_x, :]
                
        U_bc_meas_IC1_UB = np.hstack((Exact_u_IC1_UB.flatten()[:,None], Exact_v_IC1_UB.flatten()[:,None]))  
        U_bc_meas_IC1_LR = np.hstack((Exact_u_IC1_LR.flatten()[:,None], Exact_v_IC1_LR.flatten()[:,None]))  
        U_bc_meas_IC1 = np.vstack((U_bc_meas_IC1_UB, U_bc_meas_IC1_LR))

        Exact_u_IC2_UB = Exact_u_IC2_2[idx_x, :, :]
        Exact_u_IC2_LR = Exact_u_IC2_2[:, idx_x, :]
        Exact_v_IC2_UB = Exact_v_IC2_2[idx_x, :, :]
        Exact_v_IC2_LR = Exact_v_IC2_2[:, idx_x, :]
                
        U_bc_meas_IC2_UB = np.hstack((Exact_u_IC2_UB.flatten()[:,None], Exact_v_IC2_UB.flatten()[:,None]))  
        U_bc_meas_IC2_LR = np.hstack((Exact_u_IC2_LR.flatten()[:,None], Exact_v_IC2_LR.flatten()[:,None]))  
        U_bc_meas_IC2 = np.vstack((U_bc_meas_IC2_UB, U_bc_meas_IC2_LR))

        Exact_u_IC3_UB = Exact_u_IC3_2[idx_x, :, :]
        Exact_u_IC3_LR = Exact_u_IC3_2[:, idx_x, :]
        Exact_v_IC3_UB = Exact_v_IC3_2[idx_x, :, :]
        Exact_v_IC3_LR = Exact_v_IC3_2[:, idx_x, :]
                
        U_bc_meas_IC3_UB = np.hstack((Exact_u_IC3_UB.flatten()[:,None], Exact_v_IC3_UB.flatten()[:,None]))  
        U_bc_meas_IC3_LR = np.hstack((Exact_u_IC3_LR.flatten()[:,None], Exact_v_IC3_LR.flatten()[:,None]))  
        U_bc_meas_IC3 = np.vstack((U_bc_meas_IC3_UB, U_bc_meas_IC3_LR))
       
        U_bc_meas = np.stack((U_bc_meas_IC1, U_bc_meas_IC2, U_bc_meas_IC3), axis = -1)
        
        # Collocation points
        N_f = 40000    
        X_f_train = lb + (ub-lb)*lhs(X_U_meas.shape[1], N_f)
        
        # periodic bc
        X_l = np.hstack((xx[:, 0, :].flatten()[:,None], yy[:, 0, :].flatten()[:,None],
                              tt[:, 0, :].flatten()[:,None]))
        X_r = np.hstack((xx[:, -1, :].flatten()[:,None], yy[:, -1, :].flatten()[:,None],
                              tt[:, -1, :].flatten()[:,None]))
        X_u = np.hstack((xx[-1, :, :].flatten()[:,None], yy[-1, :, :].flatten()[:,None],
                              tt[-1, :, :].flatten()[:,None]))
        X_b = np.hstack((xx[0, :, :].flatten()[:,None], yy[0, :, :].flatten()[:,None],
                              tt[0, :, :].flatten()[:,None]))

        N_bc_sampled = 2500
        idx_l = np.random.choice(X_l.shape[0], N_bc_sampled, replace = False)
        idx_r = np.random.choice(X_r.shape[0], N_bc_sampled, replace = False)
        idx_u = np.random.choice(X_u.shape[0], N_bc_sampled, replace = False)
        idx_b = np.random.choice(X_b.shape[0], N_bc_sampled, replace = False)
        X_bc_sampled = np.vstack((X_l[idx_l, :], X_r[idx_r, :], X_u[idx_u, :], X_b[idx_b, :]))
        X_f_train = np.vstack((X_f_train, X_U_meas, X_bc_sampled, X_bc_meas))
                
        # add noise
        noise = 0.1
        U_train = U_train + noise*np.std(U_train, axis = 0)*np.random.randn(U_train.shape[0], U_train.shape[1], U_train.shape[2])
        U_bc_meas = U_bc_meas + noise*np.std(U_bc_meas, axis = 0)*np.random.randn(U_bc_meas.shape[0], U_bc_meas.shape[1], U_bc_meas.shape[2])
                
# =============================================================================
#         train model
# =============================================================================
        model = PhysicsInformedNN(X_U_train, U_train, X_f_train, layers_s, layers_i, lb, ub, X_l, X_r, X_u, X_b, X_bc_meas, U_bc_meas)
        model.train() 
        
# =============================================================================
#         check if training efforts are sufficient
# =============================================================================
        f = open("stdout_ADO.txt", "a+")  
        elapsed = time.time() - start_time                
        f.write('Training time: %.4f \n' % (elapsed))

        U_train_Pred = model.predict(X_U_train)   
        error_U_train = np.linalg.norm(np.reshape(U_train_Pred - U_train, (-1,1)),2) / \
            np.linalg.norm(np.reshape(U_train, (-1,1)),2)   
        f.write('Train Error u: %e \n' % (error_U_train))    

        ######################## Plots for ADO #################            
        fig = plt.figure()
        plt.plot(loss_u_history[1:])
        plt.plot(loss_v_history[1:])
        plt.legend(['loss_u', 'loss_v'])
        plt.yscale('log')       
        plt.xlabel('10x')
        plt.title('loss_U history of ADO')  
        plt.savefig('1_ADO.png')
        
        fig = plt.figure()
        plt.plot(loss_f_u_history[1:])
        plt.plot(loss_f_v_history[1:])
        plt.legend(['loss_f_u', 'loss_f_v'])
        plt.yscale('log')       
        plt.xlabel('10x')
        plt.title('loss_f history of ADO')     
        plt.savefig('2_ADO.png')
        
        fig = plt.figure()
        plt.plot(loss_bc_all_history[1:])
        plt.yscale('log')       
        plt.xlabel('10x')
        plt.title('loss_bc_all_history of ADO')  
        plt.savefig('3_ADO.png')
        
        ########################## Plots for Ridge #######################
        
        fig = plt.figure()
        plt.plot(loss_u_best_history_STRidge[1:])
        plt.yscale('log')       
        plt.title('loss_u_best_history_STRidge ')
        plt.savefig('4_ADO.png')
        
        fig = plt.figure()
        plt.plot(loss_f_u_best_history_STRidge[1:])
        plt.yscale('log')       
        plt.title('loss_f_u_best_history_STRidge')  
        plt.savefig('5_ADO.png')
        
        fig = plt.figure()
        plt.plot(loss_lambda_u_best_history_STRidge[1:])
        plt.yscale('log')       
        plt.title('loss_lambda_u_best_history_STRidge')
        plt.savefig('6_ADO.png')
        
        fig = plt.figure()
        plt.plot(tol_u_best_history_STRidge[1:])
        plt.title('tol_u_best_history_STRidge')
        plt.savefig('7_ADO.png')
                
        fig = plt.figure()
        for i in range(lambda_normalized_u_best_history_STRidge.shape[0]):
            plt.plot(lambda_normalized_u_best_history_STRidge[i, 1:])
        plt.title('lambda_normalized_u_best_history_STRidge')
        plt.savefig('8_ADO.png')
                
        fig = plt.figure()
        plt.plot(loss_v_best_history_STRidge[1:])
        plt.yscale('log')       
        plt.title('loss_v_best_history_STRidge ')
        plt.savefig('9_ADO.png')
        
        fig = plt.figure()
        plt.plot(loss_f_v_best_history_STRidge[1:])
        plt.yscale('log')       
        plt.title('loss_f_v_best_history_STRidge')  
        plt.savefig('10_ADO.png')
        
        fig = plt.figure()
        plt.plot(loss_lambda_v_best_history_STRidge[1:])
        plt.yscale('log')       
        plt.title('loss_lambda_v_best_history_STRidge')
        plt.savefig('11_ADO.png')
        
        fig = plt.figure()
        plt.plot(tol_v_best_history_STRidge[1:])
        plt.title('tol_v_best_history_STRidge')
        plt.savefig('12_ADO.png')
                
        fig = plt.figure()
        for i in range(lambda_normalized_v_best_history_STRidge.shape[0]):
            plt.plot(lambda_normalized_v_best_history_STRidge[i, 1:])
        plt.title('lambda_normalized_v_best_history_STRidge')
        plt.savefig('13_ADO.png')

# =============================================================================
#       compare w ground truth if training efforts are sufficient
# =============================================================================
        # system response
        N_U_t = 61
        d_t = np.floor((t.shape[0])/(N_U_t - 1))
        idx_t = (np.arange(N_U_t)*d_t).astype(int)

        N_U_x = 101
        d_x = np.floor((x.shape[0])/(N_U_x - 1))
        idx_x = (np.arange(N_U_x)*d_x).astype(int) 
        X_U_full, U_full = DownsampleMeas(idx_x, idx_t, xx, yy, tt, Exact_u_IC1, Exact_v_IC1, Exact_u_IC2, Exact_v_IC2, Exact_u_IC3, Exact_v_IC3, XU_flag = True)
        
        U_Full_Pred = model.predict(X_U_full)  
        error_U = np.linalg.norm(np.reshape(U_full - U_Full_Pred, (-1,1)),2)/np.linalg.norm(np.reshape(U_full, (-1,1)),2)   
        f.write('Full Field Error u: %e \n' % (error_U))    
                    
        # Lambda_u
        lambda_u_pred = model.tf_dict[model.lambda_u_tf]      
        lambda_u = np.zeros_like(lambda_u_pred)
        lambda_u[0] = 1 # lap(u)
        lambda_u[2] = 0.01 # 1
        lambda_u[9] = 1 # u
        lambda_u[23] = -1 # u**3
        lambda_u[30] = -1 # v
        
        nonzero_ind_u = np.nonzero(lambda_u)
        lambda_error_vector_u = np.absolute((lambda_u[nonzero_ind_u]-lambda_u_pred[nonzero_ind_u])/lambda_u[nonzero_ind_u])
        lambda_error_mean_u = np.mean(lambda_error_vector_u)*100
        lambda_error_std_u = np.std(lambda_error_vector_u)*100
            
        f.write('lambda_error_mean_u: %.2f%% \n' % (lambda_error_mean_u))
        f.write('lambda_error_std_u: %.2f%% \n' % (lambda_error_std_u))

        disc_eq_temp = []
        for i_lib in range(len(model.lib_descr)):
            if lambda_u[i_lib] != 0:
                disc_eq_temp.append(str(lambda_u_pred[i_lib,0]) + model.lib_descr[i_lib])

        disc_eq_u = '+'.join(disc_eq_temp)        
        f.write('The discovered equation: u_t = ' + disc_eq_u)        

        lambda_u_error = np.linalg.norm(lambda_u-lambda_u_pred,2)/np.linalg.norm(lambda_u,2)
        f.write('Lambda_u L2 Error: %e \n' % (lambda_u_error))   

        # lambda_v
        lambda_v_pred = model.tf_dict[model.lambda_v_tf]    
        lambda_v = np.zeros_like(lambda_v_pred)
        lambda_v[1] = 100 # lap(v)
        lambda_v[9] = 0.25 # u
        lambda_v[30] = -0.25 # v
    
        lambda_v_error = np.linalg.norm(lambda_v-lambda_v_pred,2)/np.linalg.norm(lambda_v,2)
        f.write('Lambda_v L2 Error: %e \n' % (lambda_v_error))   
        
        nonzero_ind_v = np.nonzero(lambda_v)
        lambda_error_vector_v = np.absolute((lambda_v[nonzero_ind_v]-lambda_v_pred[nonzero_ind_v])/lambda_v[nonzero_ind_v])
        lambda_error_mean_v = np.mean(lambda_error_vector_v)*100
        lambda_error_std_v = np.std(lambda_error_vector_v)*100
            
        print('lambda_error_mean_v: %.2f%% \n' % (lambda_error_mean_v))
        print('lambda_error_std_v: %.2f%% \n' % (lambda_error_std_v))
    
        disc_eq_temp = []
        for i_lib in range(len(model.lib_descr)):
            if lambda_v[i_lib] != 0:
                disc_eq_temp.append(str(lambda_v_pred[i_lib,0]) + model.lib_descr[i_lib])
        disc_eq_v = '+'.join(disc_eq_temp)        
        print('The discovered equation: v_t = ' + disc_eq_v)

        f.close()
        
        scipy.io.savemat('DiscLam_ADO.mat', {'Lamu_True': lambda_u, 'Lamu_Disc': lambda_u_pred,
                                         'Lamv_True': lambda_v, 'Lamv_Disc': lambda_v_pred})

        scipy.io.savemat('Histories_ADO.mat', {'lambda_u_history_STRidge': lambda_u_history_STRidge,
                                         'ridge_u_append_counter_STRidge': ridge_u_append_counter_STRidge,
                                         'lambda_v_history_STRidge': lambda_v_history_STRidge,
                                         'ridge_v_append_counter_STRidge': ridge_v_append_counter_STRidge})

        scipy.io.savemat('PredSol_ADO.mat', {'U_Full_Pred': U_Full_Pred, 'U_star': U_full})
                         
        ########################## Plots for Lambda ########################
        fig = plt.figure()
        plt.plot(lambda_u)
        plt.plot(lambda_u_pred, '--')
        plt.title('lambda_u values')
        plt.legend(['the true', 'the identified'])
        plt.savefig('14_ADO.png')
        
        fig = plt.figure()
        plt.plot(lambda_v)
        plt.plot(lambda_v_pred, '--')
        plt.title('lambda_v values')
        plt.legend(['the true', 'the identified'])
        plt.savefig('15_ADO.png')   
        
