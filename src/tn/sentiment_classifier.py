"""
@author sanjeethr, oligoglot

Implements SGDClassifier using FeatureUnions for Sentiment Classification of text
It also has code to experiment with hyper tuning parameters of the classifier
"""
from __future__ import print_function

import numpy as np
import pickle
import json
from pprint import pprint
from time import time
import sys, os

from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer, HashingVectorizer, CountVectorizer
from sklearn.metrics import classification_report
from sklearn.pipeline import FeatureUnion
from sklearn.pipeline import Pipeline
from sklearn.svm import SVC
from sklearn.linear_model import SGDClassifier
from sklearn.model_selection import RandomizedSearchCV
from scipy.stats import uniform
from sklearn.model_selection import GridSearchCV
from libindic.soundex import Soundex


from lib.feature_utils import load_docs, get_emojis_from_text, get_doc_len_range
sys.path.append(os.path.join(os.path.dirname(sys.path[0]),'extern', 'indic_nlp_library'))
from indicnlp.normalize.indic_normalize import BaseNormalizer
sys.path.append(os.path.join(os.path.dirname(sys.path[0]),'extern'))
import deepchar
sys.path.append(os.path.join(os.path.dirname(sys.path[0]),'extern', 'solthiruthi-sothanaikal'))
from symspellpy import SymSpell, Verbosity

try:
    from indictrans import Transliterator
except ImportError:
    print('Please install indic-trans from git: https://github.com/libindic/indic-trans')

class ItemSelector(BaseEstimator, TransformerMixin):
    """For data grouped by feature, select subset of data at a provided key.

    The data is expected to be stored in a 2D data structure, where the first
    index is over features and the second is over samples.  i.e.

    >> len(data[key]) == n_samples

    Please note that this is the opposite convention to scikit-learn feature
    matrixes (where the first index corresponds to sample).

    ItemSelector only requires that the collection implement getitem
    (data[key]).  Examples include: a dict of lists, 2D numpy array, Pandas
    DataFrame, numpy record array, etc.

    >> data = {'a': [1, 5, 2, 5, 2, 8],
               'b': [9, 4, 1, 4, 1, 3]}
    >> ds = ItemSelector(key='a')
    >> data['a'] == ds.transform(data)

    ItemSelector is not designed to handle data grouped by sample.  (e.g. a
    list of dicts).  If your data is structured this way, consider a
    transformer along the lines of `sklearn.feature_extraction.DictVectorizer`.

    Parameters
    ----------
    key : hashable, required
        The key corresponding to the desired value in a mappable.
    """
    def __init__(self, key):
        self.key = key

    def fit(self, x, y=None):
        return self

    def transform(self, data_dict):
        return data_dict[self.key]


class TextStats(BaseEstimator, TransformerMixin):
    """Extract features from each document for DictVectorizer"""

    def fit(self, x, y=None):
        return self

    def transform(self, reviews):
        return [{'length': len(text),
                 'num_sentences': text.count('.')}
                for text in reviews]

class FeatureExtractor(BaseEstimator, TransformerMixin):
    """Extract review text, emojis and emoji sentiment.

    Takes a sequence of strings and produces a dict of values.  Keys are
    `review`, `emojis`, and `emoji-sentiment`.
    """
    def __init__(self, lang = 'ta'):
        self.lang = lang
        self.normalizer = BaseNormalizer(lang)
        # This language map was created using Google's googletrans module. Create the file alltextlang.txt by calling
        # detect_lang_and_store in feature_utils.py
        self.lmap = self.load_language_maps( os.path.join(os.path.dirname(sys.path[0]),'../resources/data/alltextslang.txt'))
        self.soundexer = Soundex()
        self.ta_trans = Transliterator(source='eng', target='tam', build_lookup=True)
        self.ml_trans = Transliterator(source='eng', target='mal', build_lookup=True)
        self.sym_spell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        self.sym_spell.load_dictionary('../../src/extern/data/etymdict.csv.vocab.tsv.gz',
                                term_index=0,
                                count_index=1,
                                separator="\t")
        super().__init__()

    def load_language_maps(self, mapfile):
        lmap = {}
        with open(mapfile, 'r') as mapf:
            for line in mapf:
                text, lang, conf = line.rstrip().split('\t')
                lmap[text] = (lang, float(conf))
        return lmap
                    
    def get_language_tag(self, text):
        return self.lmap.get(text, ('unknown', 0.0))

    def fit(self, x, y=None):
        return self

    def transform(self, reviews):
        features = np.recarray(shape=(len(reviews),), 
                                dtype=[('review', object), ('emojis', object), ('emoji_sentiment', object), 
                                        ('lang_tag', object), ('len_range', object), ('soundexes', object),],)
        for i, review in enumerate(reviews):       
            features['review'][i] = self.normalizer.normalize(text = review)

            emojis, sentiment = get_emojis_from_text(review)
            features['emojis'][i] = ' '.join(emojis)
            features['emoji_sentiment'][i] = sentiment

            lang, conf = self.get_language_tag(review.strip())
            if lang == self.lang or lang == (self.lang + 'en'):
                # google agrees with some confidence
                agreement = 1
            elif conf < 0.5:
                # google says not-tamil, but weakly
                agreement = 0.5
            else:
                # google clearly says not-tamil
                agreement = 0
            features['lang_tag'][i] = {'lang': lang, 'agreement': agreement}
            features['len_range'][i] = get_doc_len_range(review)
            if self.lang == 'ta':
                review_trans = self.ta_trans.transform(review)
                for word in review_trans.split():
                    suggestions = self.sym_spell.lookup(word, Verbosity.CLOSEST, max_edit_distance=2, include_unknown=True)
                    if len(suggestions) > 0 and suggestions[0].distance < 3:
                        print(word, suggestions[0].term)
                        # no match with dictionary, we need a more comprehensive dictionary plus phonetic similarity
            elif self.lang == 'ml':
                review_trans = self.ml_trans.transform(review)
            else:
                review_trans = review
            # TODO: introduce spell correct here for added normalisation
            # print(lang, review_trans)
            features['soundexes'][i] = ' '.join([self.soundexer.soundex(word) for word in review_trans.split()])
        return features

def fit_predict_measure(mode, train_file, test_file, inputfile, lang = 'ta'):
    print(train_file, test_file)
    data_train = load_docs(train_file, mode='train')
    data_test = load_docs(test_file, mode=mode)
    print('Data Loaded')
    target_names = data_train['target_names']
    if mode == 'experiment':
      perform_hyper_param_tuning(data_train, data_test, inputfile, lang)
    if mode == 'test':
        pipeline = get_pipeline(lang, len(data_train['data']))
        pipeline.fit(data_train['data'], data_train['target_names'])
        """ params = pipeline.get_params(deep=True)
        print(params['rsrch__estimator__alpha'], params['rsrch__estimator__penalty']) """
        y = pipeline.predict(data_test['data'])
        print(len(y))
        assert(len(data_test['data'])==len(y))
        # TODO: TypeError: can't pickle module objects.
        # pickle.dump(pipeline, open(inputfile, 'wb'))
        idx = 0
        for v in data_test['data']:
            if (y[idx] == data_test['target_names'][idx]):
                print("Right : {} -> Prediction : {} -> Original : {}".format(v, y[idx], data_test['target_names'][idx]))
            else:
                print("Wrong : {} -> Prediction : {} -> Original : {}".format(v, y[idx], data_test['target_names'][idx]))
            idx += 1

        print(classification_report(y, data_test['target_names']))
    if mode == 'predict':
        pipeline = pickle.load(open(inputfile, 'rb'))
        pipeline.fit(data_train['data'], data_train['target_names'])
        """ params = pipeline.get_params(deep=True)
        print(params['rsrch__estimator__alpha'], params['rsrch__estimator__penalty']) """
        y = pipeline.predict(data_test['data'])
        print(len(y))
        assert(len(data_test['data'])==len(y))
        with open(f'theedhumnandrum_{lang}.tsv', 'w') as outf:
            outf.write('id\ttext\tlabel\n')
            for idx, review, label in zip(data_test['ids'], data_test['data'], y):
                print(idx)
                outf.write('\t'.join((idx, review, label)) + '\n')
        print(f'predict data written to theedhumnandrum_{lang}.tsv')


# Perform tuning of hyper parameters by passing in the field you want to
# tune as a json input file. You can find sample files in the config directory
def perform_hyper_param_tuning(data_train, data_test, input_file, lang = 'ta'):
  pipeline = get_pipeline(lang, len(data_train['data']))
  # parameters = {
  #   'sgd__loss' : ["hinge", "log", "squared_hinge", "modified_huber"],
  #   'sgd__alpha' : [0.0001, 0.001, 0.01, 0.1],
  #   'sgd__penalty' : ["l2", "l1", "none"],
  # }
  with open(input_file) as f:
     parameters = json.load(f)
  grid_search = GridSearchCV(pipeline, parameters, n_jobs=-1, verbose=1, scoring='accuracy')
  print("Performing grid search...")
  print("pipeline:", [name for name, _ in pipeline.steps])
  print("parameters:")
  pprint(parameters)
  t0 = time()
  grid_search.fit(data_train['data'], data_train['target_names'])
  print("done in %0.3fs" % (time() - t0))
  print()

  print("Best score: %0.3f" % grid_search.best_score_)
  print("Best parameters set:")
  best_parameters = grid_search.best_estimator_.get_params()
  for param_name in sorted(parameters.keys()):
      print("\t%s: %r" % (param_name, best_parameters[param_name]))
  print("Grid scores on development set:")
  print()
  means = grid_search.cv_results_['mean_test_score']
  stds = grid_search.cv_results_['std_test_score']
  for mean, std, params in zip(means, stds, grid_search.cv_results_['params']):
      print("%0.3f (+/-%0.03f) for %r"
            % (mean, std * 2, params))
  print()
  print("Detailed classification report:")
  print()
  print("The model is trained on the full development set.")
  print("The scores are computed on the full evaluation set.")
  print()
  y_true, y_pred = data_test["target_names"], grid_search.predict(data_test["data"])
  print(classification_report(y_true, y_pred))
  print()

# Get the tranformer weights for a language. Use the experiment mode of the script
# to find the right hypertuning parameters
def get_transformer_weights(lang = 'ta'):
    lang_weights = {
      'ta' : { 
              'emoji_sentiment': 0.6,
              'emojis': 0.8, #higher value seems to improve negative ratings
              'review_bow': 0.0,
              'review_ngram': 1.0,
              'lang_tag': 0.6,
              'len_range': 0.0,
              'soundexes_bow': 0.5,
          },
          'ml' : { 
              'emoji_sentiment': 0.6,
              'emojis': 0.8, #higher value seems to improve negative ratings
              'review_bow': 0.0,
              'review_ngram': 1.0,
              'lang_tag': 0.7,
              'len_range': 0.5,
              'soundexes_bow': 0.5,
          }
      }
    return lang_weights[lang]

# The core function that returns the Pipeline. This is a FeatureUnion of
# a SGD Classifier. Borrows from https://scikit-learn.org/0.18/auto_examples/hetero_feature_union.html
def get_pipeline(lang = 'ta', datalen = 1000):

    chosen_weights = get_transformer_weights(lang)
    print(chosen_weights)

    """ distributions = dict(
        penalty=['l1', 'l2', 'elasticnet'],
        alpha=uniform(loc=1e-6, scale=1e-4)
    ) """

    pipeline = Pipeline([
        # Extract the review text & emojis
        ('reviewfeatures', FeatureExtractor(lang)),

        # Use FeatureUnion to combine the features from emojis and text
        ('union', FeatureUnion(
            transformer_list=[

                # Pipeline for emojis handled like a bag of words
                ('emojis', Pipeline([
                    ('selector', ItemSelector(key='emojis')),
                    ('tfidf', TfidfVectorizer(token_pattern=r'[^\s]+', stop_words=None, max_df=0.4, min_df=2, max_features=10)),
                ])),

                # Pipeline for pulling features from the post's emoji sentiment
                ('emoji_sentiment', Pipeline([
                    ('selector', ItemSelector(key='emoji_sentiment')),
                    ('vect', HashingVectorizer()),
                ])),

                # Pipeline for length of doc feature
                ('len_range', Pipeline([
                    ('selector', ItemSelector(key='len_range')),
                    ('vect', HashingVectorizer()),
                ])),

                # Pipeline for standard bag-of-words model for soundexes
                ('soundexes_bow', Pipeline([
                    ('selector', ItemSelector(key='soundexes')),
                    # Best Tamil Configuration
                    # ('tfidf', TfidfVectorizer( input='content', stop_words=None, sublinear_tf=True, max_df=0.4, min_df=1, max_features=200))
                    ('tfidf', TfidfVectorizer(token_pattern=r'[^\s]+', input='content', stop_words=None, sublinear_tf=True, max_df=0.4, min_df=1, max_features=200)),
                    ('best', TruncatedSVD(n_components=50)),
                ])),

                # Pipeline for standard bag-of-words model for review
                ('review_bow', Pipeline([
                    ('selector', ItemSelector(key='review')),
                    # Best Tamil Configuration
                    # ('tfidf', TfidfVectorizer( input='content', stop_words=None, sublinear_tf=True, max_df=0.4, min_df=1, max_features=200))
                    ('tfidf', TfidfVectorizer( input='content', stop_words=None, sublinear_tf=True, max_df=0.4, min_df=1, max_features=200)),
                    ('best', TruncatedSVD(n_components=50)),
                ])),

                # Pipeline for pulling ad hoc features from review text
                ('review_stats', Pipeline([
                    ('selector', ItemSelector(key='review')),
                    ('stats', TextStats()),  # returns a list of dicts
                    ('vect', DictVectorizer()),  # list of dicts -> feature matrix
                ])),

                # Pipeline for ngram model for review
                ('review_ngram', Pipeline([
                    ('selector', ItemSelector(key='review')),
                    #tamil - best config 
                    # ('tfidf', CountVectorizer(ngram_range=(1, 4))),
                    ('tfidf', CountVectorizer(ngram_range=(1, 4))),
                    #('tfidf', TfidfVectorizer(ngram_range=(2, 4), max_df=0.4, min_df=2, norm='l2', sublinear_tf=True)),
                ])),

                # Pipeline for pulling langtag features
                ('lang_tag', Pipeline([
                    ('selector', ItemSelector(key='lang_tag')),
                    ('vect', DictVectorizer()),  # list of dicts -> feature matrix
                ])),

            ],

            # weight components in FeatureUnion
            transformer_weights=chosen_weights,
        )),

        # Use an SVC/SGD classifier on the combined features
        # the value for max_iter(np.ceil(10**6/datalen)) is based on suggestion here - https://scikit-learn.org/stable/modules/sgd.html#tips-on-practical-use
        # ('sgd', SGDClassifier(loss="modified_huber", penalty="elasticnet", max_iter=np.ceil(10**6/datalen), random_state=0, alpha = 0.0001)),
        ('sgd', SGDClassifier(loss="modified_huber", penalty="elasticnet", max_iter=np.ceil(10**6/datalen), random_state=0, alpha = 0.0001)),
        # ('rsrch', RandomizedSearchCV(estimator=clf, param_distributions=distributions, cv=5, n_iter=5)),
    ])
    return pipeline

if __name__ == "__main__":
    args = sys.argv
    if len(args) < 6:
        print('Your command should be:')
        print('python sentiment_classifier.py <mode> <language code> <training file path> <test file path> <inputfilepath>')
        print('mode:predict/test/experiment, language: ta/ml')
        print('Input file path is the pickle file path for train and predict, and json file path for experiment')
        sys.exit()
    mode, lang, train_file, test_file, inputfile = args[1:6]
    fit_predict_measure(mode, train_file, test_file, inputfile, lang = lang)