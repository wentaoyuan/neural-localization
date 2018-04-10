import argparse
import gym
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
from visdom import Visdom


class Model(nn.Module):
    def __init__(self, num_actions):
        super(Model, self).__init__()
        self.conv1 = torch.nn.Conv2d(5, 16, kernel_size=(3, 3))
        self.conv2 = torch.nn.Conv2d(16, 16, kernel_size=(3, 3))
        self.policy = torch.nn.Linear(16, num_actions)
        self.value = torch.nn.Linear(16, 1)
        layers = [self.conv1, self.conv2, self.policy, self.value]
        for layer in layers:
            torch.nn.init.xavier_normal(layer.weight)
            torch.nn.init.constant(layer.bias, 0)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = x.view(x.size(0), -1)
        log_pi = F.log_softmax(self.policy(x), dim=-1)
        v = self.value(x)
        return log_pi, v


class A2C:
    # Implementation of N-step Advantage Actor Critic.

    def __init__(self, env, n, use_cuda=False):
        # Initializes A2C.
        # Args:
        # - env: Gym environment.
        # - lr: Learning rate for the model.
        # - n: The value of N in N-step A2C.
        self.env = env
        self.n = n
        self.model = Model(env.action_space.n)
        self.use_cuda = use_cuda
        if use_cuda:
            self.model.cuda()

    def _array2var(self, array, requires_grad=True):
        var = Variable(torch.Tensor(array), requires_grad)
        if self.use_cuda:
            var = var.cuda()
        return var

    def train(self, lr):
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr)
        policy_losses = np.zeros(args.train_episodes)
        value_losses = np.zeros(args.train_episodes)
        lengths = np.zeros(args.train_episodes)
        rewards_mean = np.zeros(args.train_episodes // args.episodes_per_eval + 1)
        rewards_std = np.zeros(args.train_episodes // args.episodes_per_eval + 1)
        rewards_mean[0], rewards_std[0] = self.eval(args.test_episodes)
        print('episode', 0, 'reward average', rewards_mean[0], 'reward std', rewards_std[0])
        plt.xlabel('episodes')
        plt.ylabel('average reward')
        errbar = plt.errorbar(np.arange(1), rewards_mean[:1], rewards_std[:1], capsize=3)

        viz = Visdom()
        policy_loss_plot = None
        value_loss_plot = None
        length_plot = None
        reward_plot = viz.matplot(plt, env=args.task_name)

        for i in range(args.train_episodes):
            policy_losses[i], value_losses[i], lengths[i] = self.train_one_episode(args.gamma)
            if (i + 1) % args.episodes_per_plot == 0:
                if policy_loss_plot is None:
                    opts = dict(xlabel='episodes', ylabel='policy loss')
                    policy_loss_plot = viz.line(X=np.arange(1, i + 2), Y=policy_losses[:i + 1],
                                                env=args.task_name, opts=opts)
                else:
                    viz.line(X=np.arange(i - args.episodes_per_plot + 1, i + 2),
                             Y=policy_losses[i - args.episodes_per_plot:i + 1],
                             env=args.task_name, win=policy_loss_plot, update='append')
                if value_loss_plot is None:
                    opts = dict(xlabel='episodes', ylabel='value loss')
                    value_loss_plot = viz.line(X=np.arange(1, i + 2), Y=value_losses[:i + 1],
                                               env=args.task_name, opts=opts)
                else:
                    viz.line(X=np.arange(i - args.episodes_per_plot + 1, i + 2),
                             Y=value_losses[i - args.episodes_per_plot:i + 1],
                             env=args.task_name, win=value_loss_plot, update='append')
                if length_plot is None:
                    opts = dict(xlabel='episodes', ylabel='episode length')
                    length_plot = viz.line(X=np.arange(1, i + 2), Y=lengths[:i + 1],
                                           env=args.task_name, opts=opts)
                else:
                    viz.line(X=np.arange(i - args.episodes_per_plot + 1, i + 2),
                             Y=lengths[i - args.episodes_per_plot:i + 1],
                             env=args.task_name, win=length_plot, update='append')
            if (i + 1) % args.episodes_per_eval == 0:
                j = (i + 1) // args.episodes_per_eval
                rewards_mean[j], rewards_std[j] = self.eval(args.test_episodes)
                print('episode', i + 1, 'policy loss', policy_losses[i], 'value loss', value_losses[i],
                      'reward average', rewards_mean[j], 'reward std', rewards_std[j])
                errbar.remove()
                errbar = plt.errorbar(np.arange(j + 1) * args.episodes_per_eval,
                                      rewards_mean[:j + 1], rewards_std[:j + 1], capsize=3)
                viz.matplot(plt, env=args.task_name, win=reward_plot)
        plt.savefig('figs/' + args.task_name + '_rewards.png')
        torch.save(a2c.model.state_dict(), 'models/' + args.task_name + '.model')

    def train_one_episode(self, gamma):
        # Trains the model on a single episode using A2C.
        rewards, log_pi, value = self.generate_episode()
        T = len(rewards)
        R = np.zeros(T, dtype=np.float32)
        for t in reversed(range(T)):
            v_end = value.data[t + self.n] if t + self.n < T else 0
            R[t] = gamma ** self.n * v_end + \
                   sum([gamma ** k * rewards[t+k] for k in range(min(self.n, T - t))])
        R = self._array2var(R, requires_grad=False)
        policy_loss = (-log_pi * (R - value.detach())).mean()
        value_loss = ((R - value) ** 2).mean()
        loss = policy_loss + value_loss
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
        return policy_loss.data[0], value_loss.data[0], T

    def eval(self, num_episodes, stochastic=True):
        # Tests the model on n episodes
        cum_rewards = np.zeros(num_episodes)
        for i in range(num_episodes):
            rewards = self.generate_episode(stochastic)[0]
            cum_rewards[i] = np.sum(rewards)
        return cum_rewards.mean(), cum_rewards.std()

    def select_action(self, state, stochastic):
        # Select the action to take by sampling from the policy model
        # Returns
        # - the action
        # - log probability of the chosen action (as a Variable)
        # - value of the state (as a Variable)
        log_pi, value = self.model(self._array2var(state))
        if stochastic:
            action = torch.distributions.Categorical(log_pi.exp()).sample()
        else:
            _, action = log_pi.max(0)
        return action.data[0], log_pi[action], value

    def generate_episode(self, stochastic=True):
        # Generates an episode by executing the current policy in the given env.
        # Returns:
        # - a list of rewards, indexed by time step
        # - a Variable of log probabilities
        # - a Variable of state values
        log_probs = []
        values = []
        rewards = []
        state = self.env.reset()
        done = False
        while not done:
            action, log_prob, value = self.select_action(state, stochastic)
            log_probs.append(log_prob)
            values.append(value)
            state, reward, done, _ = self.env.step(action)
            rewards.append(reward)
        return rewards, torch.cat(log_probs), torch.cat(values)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--task_name', dest='task_name',
                        default='A2C', help="Name of the experiment")
    parser.add_argument('--train_episodes', dest='train_episodes', type=int,
                        default=50000, help="Number of episodes to train on.")
    parser.add_argument('--test_episodes', dest='test_episodes', type=int,
                        default=100, help="Number of episodes to test on.")
    parser.add_argument('--episodes_per_eval', dest='episodes_per_eval', type=int,
                        default=500, help="Number of episodes between each evaluation.")
    parser.add_argument('--episodes_per_plot', dest='episodes_per_plot', type=int,
                        default=50, help="Number of episodes between each plot update.")
    parser.add_argument('-n', dest='n', type=int,
                        default=20, help="Number steps in a trace.")
    parser.add_argument('--lr', dest='lr', type=float,
                        default=0.001, help="The learning rate.")
    parser.add_argument('--gamma', dest='gamma', type=float,
                        default=0.99, help="The discount factor.")
    parser.add_argument('--seed', dest='seed', type=int,
                        default=666, help="The random seed.")
    args = parser.parse_args()

    env = gym.make('LunarLander-v2')
    env.seed(args.seed)
    torch.manual_seed(args.seed)
    a2c = A2C(env, args.n)
    a2c.train(args.lr)
