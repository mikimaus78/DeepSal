import numpy as np
import theano
import theano.tensor as T 
from theano.tensor.signal.downsample import max_pool_2d 
from sals.utils.FunctionHelper import mean_nll, mean_nneq
import time

from theano.sandbox.cuda.basic_ops import gpu_contiguous
from pylearn2.sandbox.cuda_convnet.filter_acts import FilterActs
from pylearn2.sandbox.cuda_convnet.pool import MaxPool


class LogisticRegression(object):
	''' 
		define the learning cost, evaluation error, and update 
	'''

	def __init__(self, n_in, n_out,
	 			input=None, target=None, 
				actfunc=T.nnet.softmax, 
				costfunc=mean_nll, 
				errorfunc=mean_nneq):

		if input is not None:
			self.x = input 
		else:
			self.x = T.matrix('x')

		if target is not None:
			self.y = target
		else:
			self.y = T.ivector('y')

		self.actfunc = actfunc
		self.costfunc = costfunc
		self.errorfunc = errorfunc

		self.W = theano.shared(value=np.zeros((n_in, n_out), 
			dtype = theano.config.floatX), 
			name= 'W', borrow=True)

		self.b = theano.shared(value=np.zeros((n_out,), 
			dtype = theano.config.floatX), 
			name = 'b', borrow=True)

		self.output = self.actfunc(T.dot(self.x, self.W) + self.b)

		self.params = [self.W, self.b]

	def costs(self):
		return self.costfunc(self.output, self.y)
	
	def errors(self):

		if self.y.dtype.startswith('int'):
			return self.errorfunc(self.output, self.y)
		else:
			raise NotImplementedError()

	def updates(self, learning_rate):
		'''
			return update rules
		'''
		g_W = T.grad(cost=self.costs(), wrt=self.W)
		g_b = T.grad(cost=self.costs(), wrt=self.b)
		update_w = (self.W, self.W - learning_rate * g_W)
		update_b = (self.b, self.b - learning_rate * g_b)
		updates = [update_w, update_b]
		return updates

		
class FCLayer(object):
	''' Fully-connected layer'''

	def __init__(self, n_in, n_out, input = None, 
				W_init = None, b_init = None, actfun=None, tag='') :

		print 'building model: Fully-connected layer{}, input:{}, output:{}'.format(
			tag, (np.nan, n_in), (np.nan, n_out)) 
		if input is not None:
			self.x = input 
		else:
			self.x = T.matrix('x')

		if W_init is None:

			wbound = np.sqrt(6./(n_in + n_out))

			if actfun is T.nnet.sigmoid: wbound *= 4

			rng = np.random.RandomState(1000)
			W_values =  np.asarray(rng.uniform(low = -wbound, high= wbound, 
				size=(n_in, n_out)), dtype = theano.config.floatX)							

			self.W = theano.shared(value = W_values, name = 'W'+tag, borrow = True)

		else:

			self.W = W_init

		if b_init is None:
			
			b_values = np.zeros((n_out,), dtype = theano.config.floatX)
			
			self.b = theano.shared(value = b_values, name = 'b'+tag, borrow = True)

		else:
			self.b = b_init

		self.actfun = actfun

		self.params = [self.W, self.b]

	def output(self):
		# feed forward output
		y = T.dot(self.x, self.W) + self.b

		if self.actfun is None:
			return y 
		else:
			return self.actfun(y)

	def regW(self, L):

		return self.W.norm(L)/np.prod(self.W.get_value().shape)


class ConvLayer(object):
	'''
	Convolutional layer

	image_shape: (batch size, num input feature maps, image height, image width)

	filter_shape: (number of filters, num input feature maps, filter height,filter width)

	pool_shape: tuple or list of length 2

	'''


	def __init__(self, image_shape, filter_shape, pool_shape, 
			input = None, W_init = None, b_init = None, 
			actfun=None, flatten = False, tag='') :

		outL = np.floor((image_shape[-1]-filter_shape[-1]+1.)/pool_shape[-1]).astype(np.int)
		output_shape = (image_shape[0], filter_shape[0], outL, outL)
		print 'building model: Convolutional layer{}, input:{}, output:{}'.format(
			tag, image_shape, output_shape) 
		
		if input is not None:
			self.x = input 
		else:
			self.x = T.tensor4('x')

		fan_in = np.prod(filter_shape[1:])
		fan_out = (filter_shape[0] * np.prod(filter_shape[2:])/np.prod(pool_shape))

		if W_init is None:

			wbound = np.sqrt(6./(fan_in + fan_out))

			if actfun is T.nnet.sigmoid: wbound *= 4

			rng = np.random.RandomState(1000)
			W_values =  np.asarray(rng.uniform(low = -wbound, high= wbound, 
				size=filter_shape), dtype = theano.config.floatX)							

			self.W = theano.shared(value = W_values, name = 'W'+tag, borrow = True)

		else:

			self.W = W_init

		if b_init is None:
			
			b_values = np.zeros((filter_shape[0],), dtype = theano.config.floatX)
			
			self.b = theano.shared(value = b_values, name = 'b'+tag, borrow = True)

		else:
			self.b = b_init

		self.actfun = actfun
		self.flatten  = flatten
		self.filter_shape = filter_shape
		self.image_shape = image_shape
		self.pool_shape = pool_shape

		self.params = [self.W, self.b]

	def output(self):
		# convolution output
		# conv_out = T.nnet.conv.conv2d(
		# 			input=self.x, filters=self.W, 
		# 			filter_shape = self.filter_shape, 
		# 			image_shape=self.image_shape)
		
		input_shuffled = self.x.dimshuffle(1, 2, 3, 0) # bc01 to c01b
		filters_shuffled = self.W.dimshuffle(1, 2, 3, 0) # bc01 to c01b
		conv_op = FilterActs(stride=1, partial_sum=1)
		contiguous_input = gpu_contiguous(input_shuffled)
		contiguous_filters = gpu_contiguous(filters_shuffled)
		conv_out_shuffled = conv_op(contiguous_input, contiguous_filters)

		# max-pooling output
		# pooled_out = max_pool_2d(
		# 		input = conv_out,
		# 		ds = self.pool_shape,
		# 		ignore_border=True)

		pool_op = MaxPool(ds=self.pool_shape[0], stride=self.pool_shape[0])
		pooled_out_shuffled = pool_op(conv_out_shuffled)
		pooled_out = pooled_out_shuffled.dimshuffle(3, 0, 1, 2) # c01b to bc01

		y = pooled_out + self.b.dimshuffle('x', 0, 'x', 'x')

		if self.actfun is not None: y = self.actfun(y)

		if self.flatten is True:
			y = y.flatten(2)

		return y

	def regW(self, L):

		return self.W.norm(L)/np.prod(self.W.get_value().shape)


class GeneralModel(object):
	''' a wapper for general model '''
	def __init__(self, input, data, output, target, params,
					cost_func, error_func, regularizers=0, batch_size = 100):

		self.x = input
		self.ypred = output
		self.y = target
		self.params = params
		self.regularizers = regularizers
		self.cost_func = cost_func
		self.error_func = error_func

		create_incs = lambda p: theano.shared(
            np.zeros_like(p.get_value(borrow=True)), borrow=True)

		self.incs = [create_incs(p) for p in self.params]

		index = T.lscalar()
		lr = T.fscalar()
		momentum = T.fscalar()
		self.train = theano.function(inputs=[index, lr, momentum], 
			outputs=[self.costs(), self.errors(), self.outputs()], 
			updates=self.updates(lr, momentum),
			givens={
				self.x: data.train_x[index*batch_size : (index+1)*batch_size],
				self.y: data.train_y[index*batch_size : (index+1)*batch_size]
			})

		self.test = theano.function(inputs=[index,],
			outputs = [self.errors(), self.outputs()], 
			givens = {
				self.x : data.test_x[index*batch_size:(index+1)*batch_size],
				self.y : data.test_y[index*batch_size:(index+1)*batch_size]
		})

		self.valid = theano.function(inputs=[index,], 
			outputs=self.errors(), 
			givens={
				self.x: data.valid_x[index*batch_size : (index+1)*batch_size],
				self.y: data.valid_y[index*batch_size : (index+1)*batch_size]
			})


	def costs(self):

		return self.cost_func(self.ypred, self.y) + self.regularizers

	def errors(self):

		return self.error_func(self.ypred, self.y)

	def updates(self, lr, momentum):
		gparams = T.grad(cost = self.costs(), wrt = self.params)

		updates_incs = [(self.incs[p], momentum*self.incs[p] - lr*gparams[p]) 
				for p in range(len(self.params))]

		updates = [(self.params[p], self.params[p] + momentum*self.incs[p] - lr*gparams[p]) 
			for p in range(len(self.params))]
		return updates

	def outputs(self):
		return self.ypred + self.y*0


class sgd_optimizer(object):
	'''
	stochastic gradient descent optimization
	'''
	def __init__(self, data, model, batch_size=10, 
		learning_rate=0.1,
		valid_loss_delta = 1e-2,
		learning_rate_decay=0.95,
		momentum = 0.9,
		n_epochs=200):

		self.data = data 
		self.batch_size = batch_size
		if n_epochs > 0:
			self.n_epochs = n_epochs
		else:
			self.n_epochs = np.inf

		self.model = model
		self.lr = learning_rate
		self.lr_decay = learning_rate_decay
		self.valid_loss_delta = valid_loss_delta
		self.momentum = momentum

	def fit(self):

		print 'fitting ...'
		n_batches_train = self.data.train_x.get_value(borrow=True).shape[0]/self.batch_size
		n_batches_valid = self.data.valid_x.get_value(borrow=True).shape[0]/self.batch_size
		n_batches_test = self.data.test_x.get_value(borrow=True).shape[0]/self.batch_size
		index_show = np.floor(np.linspace(0, n_batches_train-1, 5))

		start_time = time.clock()
		epoch = 0
		valid_loss_prev = 2304
		while (epoch < self.n_epochs):
			epoch += 1
			#print self.model.params[0].get_value().max()
			for batch_index in range(n_batches_train):
				t0 = time.clock()
				batch_avg_cost, batch_avg_error, _ = self.model.train(batch_index, self.lr, self.momentum)
				t1 = time.clock()
				if batch_index in index_show:
					print '{0:d}.{1:02d}... cost: {2:.3f}, error: {3:.3f} ({4:.3f} sec)'.format(epoch,
						batch_index, batch_avg_cost*100/2304, batch_avg_error*100/2304, t1-t0)

			valid_avg_loss = np.mean([self.model.valid(i) for i in range(n_batches_valid)])
			test_avg_loss = np.mean([self.model.test(i)[0] for i in xrange(n_batches_test)])
			
			if valid_avg_loss/2304. < 10/100.:
				decrease = (valid_loss_prev - valid_avg_loss)/valid_loss_prev
				if decrease > self.valid_loss_delta:
					self.lr *= self.lr_decay
					valid_loss_prev = valid_avg_loss
			print '==================Test Output==================='
			print 'Update learning_rate {0:.6f}'.format(self.lr)
			print 'validation error {0:.2f}%, testing error {1:.2f}%'.format(  
				valid_avg_loss*100./2304, test_avg_loss*100./2304)
			print '================================================'

		end_time = time.clock()
		print 'The code run for %d epochs, with %f epochs/sec' % (
        			epoch, 1. * epoch / (end_time - start_time))
