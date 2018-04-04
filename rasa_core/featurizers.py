from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import io
import logging
import os

import jsonpickle
import numpy as np
from builtins import str

from rasa_core import utils
from rasa_core.events import ActionExecuted

logger = logging.getLogger(__name__)


class FeaturizeMechanism(object):
    """Transform the conversations state into machine learning formats.

    FeaturizeMecahnism decides how the bot will transform the conversation state to a
    format which a classifier can read."""

    def create_helpers(self, domain, all_input):
        # will be used in label_featurizer
        return

    def encode(self, active_features, domain):
        raise NotImplementedError("FeaturizeMechanism must have the capacity to "
                                  "encode features to a vector")

    def encode_label(self, label, domain):
        if label is None:
            return np.ones(domain.num_actions, dtype=int) * -1

        y = np.zeros(domain.num_actions, dtype=int)
        y[domain.index_for_action(label)] = 1
        return y

    @staticmethod
    def decode(feature_vec, input_feature_map, ndigits=8):
        """Reverse operation to binary_encoded_features

        :param feature_vec: binary feature vector
        :param input_feature_map: map of all features
        :param ndigits: number of digits to round to
        :return: dictionary of active features
        """

        reversed_features = []
        for bf in feature_vec:
            non_zero_feature_idxs = np.where((0 != bf) & (bf != -1))
            if np.any(non_zero_feature_idxs):
                feature_tuples = []
                for feature_idx in np.nditer(non_zero_feature_idxs):
                    feat_name = input_feature_map[feature_idx]

                    # round if necessary
                    if ndigits is not None:
                        feat_value = round(bf[feature_idx], ndigits)
                    else:
                        feat_value = bf[feature_idx]

                    # convert numpy types to primitives
                    if isinstance(feat_value, np.generic):
                        feat_value = np.asscalar(feat_value)

                    feature_tuples.append((feat_name, feat_value))
                reversed_features.append(feature_tuples)
            else:
                reversed_features.append(None)
        return reversed_features


class BinaryFeaturizeMechanism(FeaturizeMechanism):
    """Assumes all features are binary.

    All features should be either on or off, denoting them with 1 or 0."""

    def encode(self, active_features, domain):
        """Returns a binary vector indicating which features are active.

        Given a dictionary of active_features (e.g. 'intent_greet',
        'prev_action_listen',...) return a binary vector indicating which
        features of `self.input_features` are in the bag. NB it's a
        regular double precision float array type.

        For example with two active features out of five possible features
        this would return a vector like `[0 0 1 0 1]`

        If this is just a padding vector we set all values to `-1`.
        padding vectors are specified by a `None` or `[None]`
        value for active_features."""

        num_features = len(domain.input_feature_map.keys())
        if active_features is None or None in active_features:
            return np.ones(num_features, dtype=np.int32) * -1
        else:
            # we are going to use floats and convert to int later if possible
            used_features = np.zeros(num_features, dtype=float)
            using_only_ints = True
            best_intent = None
            best_intent_prob = 0.0

            for feature_name, prob in active_features.items():
                if feature_name.startswith('intent_'):
                    if prob >= best_intent_prob:
                        best_intent = feature_name
                        best_intent_prob = prob
                elif feature_name in domain.input_feature_map:
                    if prob != 0.0:
                        idx = domain.input_feature_map[feature_name]
                        used_features[idx] = prob
                        using_only_ints = using_only_ints and utils.is_int(prob)
                else:
                    logger.debug(
                            "Feature '{}' (value: '{}') could not be found in "
                            "feature map. Make sure you added all intents and "
                            "entities to the domain".format(feature_name, prob))

            if best_intent is not None:
                # finding the maximum confidence intent and
                # appending it to the active_features val
                index_in_feature_list = domain.input_feature_map.get(best_intent)
                if index_in_feature_list is not None:
                    used_features[index_in_feature_list] = 1
                else:
                    logger.warning(
                            "Couldn't set most probable feature '{}', "
                            "it wasn't found in the feature list of the domain."
                            " Make sure you added all intents and "
                            "entities to the domain.".format(best_intent))

            if using_only_ints:
                # this is an optimization - saves us a bit of memory
                return used_features.astype(np.int32)
            else:
                return used_features


class ProbabilisticFeaturizeMechanism(FeaturizeMechanism):
    """Uses intent probabilities of the NLU and feeds them into the model."""

    def encode(self, active_features, domain):
        """Returns a binary vector indicating active features,
        but with intent features given with a probability.

        Given a dictionary of active_features (e.g. 'intent_greet',
        'prev_action_listen',...) and intent probabilities
        from rasa_nlu, will be a binary vector indicating which features
        of `self.input_features` are active.

        For example with two active features and two uncertain intents out
        of five possible features this would return a vector
        like `[0.3, 0.7, 1, 0, 1]`.

        If this is just a padding vector we set all values to `-1`.
        padding vectors are specified by a `None` or `[None]`
        value for active_features."""

        num_features = len(domain.input_feature_map.keys())
        if active_features is None or None in active_features:
            return np.ones(num_features, dtype=np.int32) * -1
        else:

            used_features = np.zeros(num_features, dtype=np.float)
            for active_feature, value in active_features.items():
                if active_feature in domain.input_feature_map:
                    idx = domain.input_feature_map[active_feature]
                    used_features[idx] = value
                else:
                    logger.debug(
                            "Found feature not in feature map. "
                            "Name: {} Value: {}".format(active_feature, value))
            return used_features


class Featurizer(object):

    def __init__(self, featurize_mechanism):
        self.featurize_mechanism = featurize_mechanism

    def featurize_trackers(self, trackers, domain):
        raise NotImplementedError("Featurizer must have the capacity to "
                                  "encode features to a vector")

    def create_X(self, trackers, domain):
        raise NotImplementedError("Featurizer must have the capacity to "
                                  "create feature vector")

    def persist(self, path):
        featurizer_file = os.path.join(path, "featurizer.json")
        with io.open(featurizer_file, 'w') as f:
            f.write(str(jsonpickle.encode(self)))

    @staticmethod
    def load(path):
        featurizer_file = os.path.join(path, "featurizer.json")
        if os.path.isfile(featurizer_file):
            with io.open(featurizer_file, 'r') as f:
                _json = f.read()
            return jsonpickle.decode(_json)
        else:
            logger.info("Couldn't load featurizer for policy. "
                        "File '{}' doesn't exist. ".format(featurizer_file))
            return None


class FullDialogueFeaturizer(Featurizer):
    def __init__(self, featurize_mechanism, max_history=None):
        super(FullDialogueFeaturizer, self).__init__(featurize_mechanism)

        self.max_len = max_history

    def _calculate_max_len(self, as_states):
        self.max_len = 0
        for states in as_states:
            self.max_len = max(self.max_len, len(states))

    def _pad_states(self, states):
        # pad up to max_len or slice
        if len(states) < self.max_len:
            states += [None] * (self.max_len - len(states))
        else:
            # TODO questinable thing: to delete beginning of stories
            states = states[-self.max_len:]

        return states

    def _featurize_states(self, trackers_as_states, domain):
        """Create X"""

        features = []
        true_lengths = []

        for tracker_states in trackers_as_states:

            tracker_states = self._pad_states(tracker_states)
            dialogue_len = len(tracker_states)

            story_features = []
            for state in tracker_states:
                story_features.append(self.featurize_mechanism.encode(state, domain))

            features.append(story_features)
            true_lengths.append(dialogue_len)

        X = np.array(features)

        return X, true_lengths

    def _featurize_labels(self, trackers_as_actions, domain):
        """Create y"""

        labels = []
        for story_idx, tracker_actions in enumerate(trackers_as_actions):

            tracker_actions = self._pad_states(tracker_actions)

            story_labels = []
            for action in tracker_actions:
                story_labels.append(self.featurize_mechanism.encode_label(
                                        action, domain))
            labels.append(story_labels)

        y = np.array(labels)

        return y

    def featurize_trackers(self, trackers, domain):
        """Create training data"""

        trackers_as_actions = []
        trackers_as_states = []

        for tracker in trackers:
            states = domain.features_for_tracker_history(tracker)

            delete_first_state = False
            actions = []
            for event in tracker._applied_events():
                if isinstance(event, ActionExecuted):
                    if not event.unpredictable:
                        # only actions which can be predicted at a stories start
                        actions.append(event.action_name)
                    else:
                        # unpredictable actions can be only the first in the story
                        delete_first_state = True

            if delete_first_state:
                states = states[1:]

            trackers_as_actions.append(actions)
            trackers_as_states.append(states[:-1])

        if self.max_len is None:
            self._calculate_max_len(trackers_as_actions)

        self.featurize_mechanism.create_helpers(domain, trackers_as_states)

        X, true_lengths = self._featurize_states(trackers_as_states, domain)
        y = self._featurize_labels(trackers_as_actions, domain)

        return X, y, true_lengths

    def create_X(self, trackers, domain):
        """Create X for prediction"""
        trackers_as_states = []
        for tracker in trackers:
            states = domain.features_for_tracker_history(tracker)
            trackers_as_states.append(states)

        X, true_lengths = self._featurize_states(trackers_as_states, domain)
        return X, true_lengths


class MaxHistoryFeaturizer(Featurizer):
    def __init__(self, featurizer, max_history=5, remove_duplicates=True):
        super(MaxHistoryFeaturizer, self).__init__(featurizer)

        self.max_history = max_history

        self.remove_duplicates = remove_duplicates

    def featurize_trackers(self, trackers, domain):
        features = []
        labels = []
        for tracker in trackers:
            states = domain.features_for_tracker_history(tracker)
            idx = 0
            for event in tracker._applied_events():
                if isinstance(event, ActionExecuted):
                    if not event.unpredictable:
                        # only actions which can be predicted at a stories start
                        y = self.featurize_mechanism.encode_label(
                                    event.action_name, domain)

                        prior_states = states[:idx+1]

                        feature_vector = domain.slice_feature_history(
                            self.featurize_mechanism, prior_states,
                            self.max_history)

                        features.append(feature_vector)
                        labels.append(y)
                    idx += 1

        X = np.array(features)
        y = np.array(labels)

        if self.remove_duplicates:
            logger.debug("Got {} action examples."
                         "".format(y.shape[0]))
            X, y = self._deduplicate_training_data(X, y)
            logger.debug("Deduplicated to {} unique action examples."
                         "".format(y.shape[0]))

        return X, y, [self.max_history]

    def create_X(self, trackers, domain):
        features = []
        for tracker in trackers:
            states = domain.features_for_tracker_history(tracker)

            feature_vector = domain.slice_feature_history(
                self.featurize_mechanism, states,
                self.max_history)

            features.append(feature_vector)

        X = np.array(features)

        return X, [self.max_history]

    @staticmethod
    def _deduplicate_training_data(X, y):
        # type: (ndarray, ndarray) -> Tuple[ndarray, ndarray]
        """Make sure every training example in X occurs exactly once."""

        # we need to concat X and y to make sure that
        # we do NOT throw out contradicting examples
        # (same featurization but different labels).
        # appends y to X so it appears to be just another feature
        if not utils.is_training_data_empty(X):
            casted_y = np.broadcast_to(
                    y[:, np.newaxis, :], (y.shape[0], X.shape[1], y.shape[1]))

            concatenated = np.concatenate((X, casted_y), axis=2)

            t_data = np.unique(concatenated, axis=0)
            X_unique = t_data[:, :, :X.shape[2]]
            y_unique = t_data[:, 0, X.shape[2]:]
            return X_unique, y_unique
        else:
            return X, y