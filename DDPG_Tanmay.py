import gym

import random
import numpy as np
import pickle
from tensorflow import keras
# from keras.models import Sequential
# from keras.layers import Dense
from keras.optimizers import Adam
import keras.backend as K

import os
import imageio
from PIL import Image
import PIL.ImageDraw as ImageDraw

from Noise import OUNoise

import matplotlib.pyplot as plt

from collections import deque

import tensorflow as tf
# import tensorflow.compat.v1 as tf
#
# tf.disable_v2_behavior()
#
# NUM_PARALLEL_EXEC_UNITS = 12
#
# # Assume that the number of cores per socket in the machine is denoted as NUM_PARALLEL_EXEC_UNITS
# #  when NUM_PARALLEL_EXEC_UNITS=0 the system chooses appropriate settings
#
# config = tf.ConfigProto(intra_op_parallelism_threads=NUM_PARALLEL_EXEC_UNITS,
#                         inter_op_parallelism_threads=4,
#                         allow_soft_placement=True,
#                         device_count={'CPU': NUM_PARALLEL_EXEC_UNITS})
#
# session = tf.Session(config=config)


live_plot = False

seed = 16 #random.randint(0,100) #58 is nice #2021 #78
num_episodes = 201
max_steps = 1000  # Maximum number of steps in an episode
min_steps = max_steps
exploring_starts = 1
average_of = 100

step_decay = 1  # 0.995
augment = 0  # 0.001

render_list = [0, 10, 25, 50, 100, 120, 150, 200] #, 300, 500, 1000, 1500, 1997, 1998, 1999, 2000]#0, 10, 20, 30, 40, 50, 100, 110, 120, 130, 140, 150 ]  # 50, 51, 52, 53, 100, 101, 102, 103, 104, 105] #0, 10, 20, 30, 31, 32, 33, 34, 35]
save = True


class Actor(keras.Model):
    '''Attempted model'''
    pass
    # @tf.function
    # def train_step(self, data):
    #     state, critic_value = data
    #     with tf.GradientTape() as tape:
    #     # actions = self.model(states, training=True)
    #     # critic_value = self.state_action_model(states_actions, training=True)
    #     #     critic_value = self.state_action_model(states_actions)
    #         actor_loss = -tf.math.reduce_mean(critic_value)
    #
    #     actor_grad = tape.gradient(actor_loss, self.trainable_variables)
    #     self.optimizer.apply_gradients(zip(actor_grad, self.trainable_variables))

class Critic(keras.Model):
    pass
    # @tf.function
    # def train_step(self, data):
    #     y_s, y_pred = data
    #     with tf.GradientTape() as tape:
    #         y = self(y_s, training=True)  # Forward pass
    #         # Compute the loss value
    #         # (the loss function is configured in `compile()`)
    #         loss = self.compiled_loss(y, y_pred, regularization_losses=self.losses)
    #
    #     critic_grad = tape.gradient(loss, self.trainable_variables)
    #     self.optimizer.apply_gradients(zip(critic_grad, self.trainable_variables))


class Agent:
    '''The most perfect DDPG Agent you have ever seen'''
    # Parameters taken from various sources
    epsilon = 0
    epsilon_min = 0
    decay = 0.9

    learn_start = 1000
    gamma = 0.99
    alpha = 0.002
    tau = 0.005

    mem_len = 1e5
    memory = deque(maxlen=int(mem_len))

    def __init__(self, env, seed):

        self.env = env
        random.seed(seed)
        np.random.seed(seed)
        tf.random.set_seed(seed)
        self.env.seed(seed)
        self.actor = self.createModel()
        self.target_actor = self.createModel()
        self.noise = OUNoise(self.env.action_space.shape[0], seed, theta=0.2, sigma=0.5)  # noise is actually OpenAI baselines OU Noise wrapped in another OUNoise function
        self.critic = self.createModel((self.env.observation_space.shape[0], self.env.action_space.shape[0]))
        self.target_critic = self.createModel((self.env.observation_space.shape[0], self.env.action_space.shape[0]))
        self.target_critic.set_weights(self.critic.get_weights()) #ensure inital weights are equal for networks
        self.target_actor.set_weights(self.actor.get_weights())
        self.reset()
        # return self.actor


    def createModel(self, input=None):
        '''Generate neural network models based on inputs, defaults to Actor model'''
        last_init = tf.random_uniform_initializer(minval=-0.003, maxval=0.003)  # To prevent actor network from causing steep gradients
        if input is None:
            input = self.env.observation_space.shape[0] # Actor
            inputs = keras.layers.Input(shape=(input,))
            hidden = keras.layers.Dense(256, activation="relu")(inputs)
            hidden = keras.layers.Dense(256, activation="relu")(hidden)
            outputs = keras.layers.Dense(1, activation="tanh", kernel_initializer=last_init)(hidden)
            model = Actor(inputs, outputs)
            lr_schedule = keras.optimizers.schedules.ExponentialDecay(
                initial_learning_rate=self.alpha / 2,
                decay_steps=1e9,
                decay_rate=1) #This could allow us to use decaying learning rate
            model.compile(loss="huber_loss", optimizer=Adam(learning_rate=lr_schedule)) #Compile model with optimizer so we can apply tape.gradient later
        else:  # Critic
            input_o, input_a = input
            input1 = keras.layers.Input(shape=(input_o,))
            input2 = keras.layers.Input(shape=(input_a,))
            input11 = keras.layers.Dense(16, activation="relu")(input1)
            input11 = keras.layers.Dense(32, activation="relu")(input11)
            input21 = keras.layers.Dense(32, activation="relu")(input2)
            cat = keras.layers.Concatenate()([input11, input21])
            hidden = keras.layers.Dense(256, activation="relu")(cat)
            hidden = keras.layers.Dense(256, activation="relu")(hidden)
            outputs = keras.layers.Dense(1, activation="linear", kernel_initializer=last_init)(hidden)
            lr_schedule = keras.optimizers.schedules.ExponentialDecay(
                initial_learning_rate=self.alpha / 1,
                decay_steps=1e9,
                decay_rate=1)
            model = Critic([input1, input2], outputs)
            model.compile(loss="mean_squared_error", optimizer=Adam(learning_rate=lr_schedule))  # mean_squared_error
        return model

    def replayBuffer(self, state, action, reward, next_state, terminal):
        ##TODO Implement prioritised buffer
        self.memory.append([state, action, reward, next_state, terminal])

    @tf.function  #EagerExecution for speeeed
    def replay(self, states, actions, rewards, next_states): #, actor, target_actor, critic, target_critic):
        '''tf function that replays sampled experience to update actor and critic networks using gradient'''
        # Very much inspired by Keras tutorial: https://keras.io/examples/rl/ddpg_pendulum/
        with tf.GradientTape() as tape:
            target_actions = self.target_actor(next_states, training=True)
            q_target = rewards + self.gamma * self.target_critic([next_states, target_actions], training=True)
            q_current = self.critic([states, actions], training=True)
            critic_loss = tf.math.reduce_mean(tf.math.square(q_target - q_current))

        critic_grad = tape.gradient(critic_loss, self.critic.trainable_variables)
        self.critic.optimizer.apply_gradients(zip(critic_grad, self.critic.trainable_variables))

        with tf.GradientTape() as tape:
            actions_pred = self.actor(states, training=True)
            q_current = self.critic([states, actions_pred], training=True)
            actor_loss = -tf.math.reduce_mean(q_current)

        actor_grad = tape.gradient(actor_loss, self.actor.trainable_variables)
        self.actor.optimizer.apply_gradients(zip(actor_grad, self.actor.trainable_variables))

    @tf.function
    def update_weight(self, target_weights, weights, tau):
        '''tf function for updating the weights of selected target network'''
        for (a, b) in zip(target_weights, weights):
            a.assign(b * tau + a * (1 - tau))

    def trainTarget(self):
        '''Standard function to update target networks by tau'''
        self.update_weight(self.target_actor.variables, self.actor.variables, self.tau)
        self.update_weight(self.target_critic.variables, self.critic.variables, self.tau)

    def sample2batch(self, batch_size = 64):
        '''Return a set of Tensor samples from the memory buffer of batch_size, default is 64'''
        # batch_size = 64

        if len(self.memory) < batch_size:  # return nothing if not enough experiences available
            return
        # Generate batch and emtpy arrays
        samples = random.sample(self.memory, batch_size)
        next_states = np.zeros((batch_size, self.env.observation_space.shape[0]))
        states = np.zeros((batch_size, self.env.observation_space.shape[0]))
        rewards = np.zeros((batch_size, 1))
        actions = np.zeros((batch_size, self.env.action_space.shape[0]))

        # Separate batch into arrays
        for idx, sample in enumerate(samples):
            state, action, reward, next_state, terminal = sample
            states[idx] = state
            actions[idx] = action
            rewards[idx] = reward
            next_states[idx] = next_state

        # Convert arrays to tensors so we can use replay as a callable TensorFlow graph
        states = tf.convert_to_tensor((states))
        rewards = tf.convert_to_tensor((rewards))
        rewards = tf.cast(rewards, dtype=tf.float32)
        actions = tf.convert_to_tensor((actions))
        next_states = tf.convert_to_tensor((next_states))

        return (states, actions, rewards, next_states)

    def train(self, state, action, reward, next_state, terminal, steps):
        '''Function call to update buffer and networks at predetermined intervals'''
        self.replayBuffer(state, action, reward, next_state, terminal)  # Add new data to buffer
        if steps % 1 == 0 and len(self.memory) > self.learn_start:  # Sample every X steps
            samples = self.sample2batch()
            states, actions, rewards, next_states = samples
            self.replay(states, actions, rewards, next_states)
        if steps % 1 == 0:  # Update targets only every X steps
            self.trainTarget()

    def reset(self):
        self.epsilon *= self.decay
        self.epsilon = max(self.epsilon_min, self.epsilon)

    def chooseAction(self, state, scale = False):
        '''Choose action based on policy and noise function. Scale option used to limit maximum actions'''
        # self.epsilon *= self.decay
        # self.epsilon = round(max(self.epsilon / 1000, self.epsilon), 5)
        # print(state[0])
        state = tf.expand_dims(tf.convert_to_tensor(state), 0) #convert to tensor for speeeed
        if np.random.random() < self.epsilon:  # If using epsilon instead of exploration noise
            return random.uniform(-1, 1)
        if scale:
            return np.clip(0.33 * (self.actor(state)) + self.noise.sample(), -1, 1)
        return np.clip(1 * tf.squeeze(self.actor(state)).numpy() + self.noise.sample(), -1, 1)  # np.argmax(self.model.predict(state))  # action


class DataStore:
    def __init__(self, averages, rewards):
        self.averages = averages
        self.rewards = rewards


def _label_with_episode_number(frame, episode_num):
    im = Image.fromarray(frame)

    drawer = ImageDraw.Draw(im)

    if np.mean(im) < 128:
        text_color = (255,255,255)
    else:
        text_color = (0,0,0)
    drawer.text((im.size[0]/20,im.size[1]/18), f'Episode: {episode_num}', fill=text_color)

    return im

def main(max_steps):
    env = gym.make('MountainCarContinuous-v0').env
    # env = gym.make('Pendulum-v0').env
    agent = Agent(env, seed)
    rewards = np.zeros(num_episodes)
    rewards_av = deque(maxlen=int(average_of))
    averages = np.ones(num_episodes)
    print(seed)
    for episode in range(num_episodes):
        action = np.zeros(1)
        state = env.reset().reshape(env.action_space.shape[0], env.observation_space.shape[0])#1, 2)
        total_reward = 0
        frames = []
        for step in range(max_steps):
            if (episode in render_list) and save:  # Render and save process
                frame = env.render(mode='rgb_array')
                frames.append(_label_with_episode_number(frame, episode_num=episode))
            elif episode in render_list:
                env.render()
                pass
            if episode < exploring_starts:
                action[0] = agent.chooseAction(state, True)
            else:
                action[0] = agent.chooseAction(state)
            next_state, reward, terminal, info = env.step(action)
            next_state = next_state.reshape(env.action_space.shape[0], env.observation_space.shape[0])
            total_reward += reward
            reward += ((state[0][0]+1.2)**2)*(augment)  # Augmentation of reward if testing
            agent.train(state, action, reward, next_state, terminal, step)  # Push data to Agent
            state = next_state
            if terminal:
                break

        agent.noise.reset()

        rewards[episode:] = total_reward
        rewards_av.append(total_reward)
        averages[episode:] = np.mean(rewards_av)

        if np.mean(rewards_av) <= 90:  # step >= 199:
            print(
                "Failed to complete in episode {:4} with reward of {:8.3f} in {:5} steps, average reward of last {:4} episodes "
                "is {:8.3f}".format(episode, total_reward, step + 1, average_of, np.mean(rewards_av)))

        else:
            print("Completed in {:4} episodes, with reward of {:8.3f}, average reward of {:8.3f}".format(episode, total_reward, np.mean(rewards_av)))
            #break

        if live_plot:
            plt.subplot(2, 1, 1)
            plt.plot(averages)
            plt.subplot(2, 1, 2)
            plt.plot(rewards)
            plt.pause(0.0001)
            plt.clf()

        max_steps = int(max(min_steps, max_steps*step_decay))

        if episode % 25 == 0:
            data = DataStore(averages, rewards)
            with open('data_ddpg.pk1', 'wb') as handle:
                pickle.dump(data, handle, pickle.HIGHEST_PROTOCOL)

        env.close()
        if frames:
            imageio.mimwrite(os.path.join('./videos/', 'agent_ep_{}.gif'.format(episode)), frames, fps=30)
            del frames

    with open('data_ddpg.pk1', 'wb') as handle:
        pickle.dump(data, handle, pickle.HIGHEST_PROTOCOL)


if __name__ == "__main__":
    main(max_steps)
