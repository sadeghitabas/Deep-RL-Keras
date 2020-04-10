import random
import numpy as np

from tqdm import tqdm
from keras.models import Model
from keras.optimizers import Adam
from keras import regularizers
from keras.utils import to_categorical
from keras.layers import Input, Dense, Flatten

from .critic import Critic
from .actor import Actor
from utils.networks import tfSummary
from utils.stats import gather_stats

from .ppo_loss import proximal_policy_optimization_loss 

class PPO:
    """ Actor-Critic Main Algorithm
    """

    def __init__(self, act_dim, env_dim, k, gamma = 0.99, lr = 0.0001, 
                loss_clipping=0.2, noise=1.0, entropy_loss=5e-3):
        """ Initialization
        """
        # PPO Params
        self.loss_clipping = loss_clipping
        self.noise = noise
        self.entropy_loss = entropy_loss

        # Environment and A2C parameters
        self.act_dim = act_dim
        self.env_dim = env_dim
        self.gamma = gamma
        self.lr = lr

        # # Create actor and critic networks
        # self.shared = self.buildNetwork()
        # self.actor = Actor(self.env_dim, act_dim, self.shared, lr)
        # self.critic = Critic(self.env_dim, act_dim, self.shared, lr)

        # # Build optimizers
        # self.a_opt = self.actor.optimizer()
        # self.c_opt = self.critic.optimizer()


        self.actor = self.build_actor()
        self.critic = Critic(self.env_dim, act_dim, lr)

        self.observation = None
        self.val = False

    # def buildNetwork(self):
    #     """ Assemble shared layers
    #     """
    #     inp = Input((self.env_dim))
    #     x = Flatten()(inp)
    #     x = Dense(64, activation='relu')(x)
    #     x = Dense(128, activation='relu')(x)
    #     return Model(inp, x)

    def save_weights(self, path):
        path += '_LR_{}'.format(self.lr)
        self.actor.save(path)
        self.critic.save(path)

    def load_weights(self, path_actor, path_critic):
        self.critic.load_weights(path_critic)
        self.actor.load_weights(path_actor)




    # ********************************************************
    # Colocar em outro lugar
    # ********************************************************

    def build_actor(self):
        HIDDEN_SIZE = 128
        NUM_LAYERS = 2
        state_input = Input(shape=(self.env_dim,))
        advantage = Input(shape=(1,))
        old_prediction = Input(shape=(self.act_dim,))

        x = Dense(HIDDEN_SIZE, activation='tanh')(state_input)
        for _ in range(NUM_LAYERS - 1):
            x = Dense(HIDDEN_SIZE, activation='tanh')(x)

        out_actions = Dense(self.act_dim, activation='softmax', name='output')(x)

        model = Model(inputs=[state_input, advantage, old_prediction], outputs=[out_actions])
        model.compile(optimizer=Adam(lr=self.lr),
                      loss=[
                          proximal_policy_optimization_loss(
                            advantage=advantage,
                            old_prediction=old_prediction,
                            loss_clipping=self.loss_clipping,
                            entropy_loss=self.entropy_loss
                          )
                        ]
                    )
        model.summary()

        return model

    def transform_reward(self):
        GAMMA = 0.99
        for j in range(len(self.reward) - 2, -1, -1):
            self.reward[j] += self.reward[j + 1] * GAMMA

    def get_action(self):
        DUMMY_ACTION, DUMMY_VALUE = np.zeros((1, self.act_dim)), np.zeros((1, 1))

        p = self.actor.predict([self.observation.reshape(1, self.env_dim), DUMMY_VALUE, DUMMY_ACTION])
        if self.val is False:
            action = np.random.choice(self.act_dim, p=np.nan_to_num(p[0]))
        else:
            action = np.argmax(p[0])
        action_matrix = np.zeros(self.act_dim)
        action_matrix[action] = 1
        return action, action_matrix, p

    def get_batch(self, env, args):
        batch = [[], [], [], []]
        
        tmp_batch = [[], [], []]

        while len(batch[0]) < args.buffer_size:
            if args.render:
                env.render()

            action, action_matrix, predicted_action = self.get_action()
            observation, reward, done, info = env.step_one(action)

            self.reward.append(reward)

            tmp_batch[0].append(self.observation)
            tmp_batch[1].append(action_matrix)
            tmp_batch[2].append(predicted_action)
            self.observation = observation

            if done:
                self.transform_reward()
                if self.val is False:
                    for i in range(len(tmp_batch[0])):
                        obs, action, pred = tmp_batch[0][i], tmp_batch[1][i], tmp_batch[2][i]
                        r = self.reward[i]
                        batch[0].append(obs)
                        batch[1].append(action)
                        batch[2].append(pred)
                        batch[3].append(r)
                tmp_batch = [[], [], []]

                # Reset Env
                self.episode += 1
                if self.episode % 100 == 0:
                    self.val = True
                else:
                    self.val = False
                self.observation = env.reset_one()
                self.reward = []

        obs, action, pred, reward = np.array(batch[0]), np.array(batch[1]), np.array(batch[2]), np.reshape(np.array(batch[3]), (len(batch[3]), 1))
        pred = np.reshape(pred, (pred.shape[0], pred.shape[2]))
        return obs, action, pred, reward

    def train(self, env, args, summary_writer):
        self.observation = env.reset_one()
        self.reward = []
        self.reward_over_time = []
        self.gradient_steps = 0
        self.episode = 1

        self.batch_rewards = []
        self.actor_losses = []
        self.critic_losses = []

        while self.episode < args.nb_episodes:
            print("Episode ", self.episode)
            obs, action, pred, reward = self.get_batch(env, args)
            obs, action, pred, reward = obs[:args.buffer_size], action[:args.buffer_size], pred[:args.buffer_size], reward[:args.buffer_size]
            old_prediction = pred
            pred_values = self.critic.predict(obs)

            advantage = reward - pred_values

            actor_loss = self.actor.fit([obs, advantage, old_prediction], [action], batch_size=args.batch_size, shuffle=True, epochs=args.epochs, verbose=False)
            critic_loss = self.critic.fit([obs], [reward], batch_size=args.batch_size, shuffle=True, epochs=args.epochs, verbose=False)
            # summary_writer.add_scalar('Actor loss', actor_loss.history['loss'][-1], self.gradient_steps)
            # summary_writer.add_scalar('Critic loss', critic_loss.history['loss'][-1], self.gradient_steps)
            
            self.batch_rewards.append(np.sum(reward))
            self.actor_losses.append(actor_loss.history['loss'])
            self.critic_losses.append(critic_loss.history['loss'])

            self.gradient_steps += 1

        return self.batch_rewards, self.actor_losses, self.critic_losses
