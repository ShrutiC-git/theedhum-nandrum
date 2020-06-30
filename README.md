# theedhum-nandrum
A sentiment classifier on mixed language (and mixed script) reviews in Tamil, Malayalam and English

## Installation
## Pre-requisites
* Python 3.7 or above
## Getting the code
----------------
* `cd /path/to/parent/`
* `git clone https://github.com/oligoglot/theedhum-nandrum.git`
* `cd theedhum-nandrum`

## Setting up dev environment
----------------
* `virtualenv venv_tn`
* `source venv_tn/bin/activate`
* `pip install -r requirements.txt `

# Steps
## Pre-processing
### Noise removal
1. Remove irrelevant parts of the data, like html tags

### Language identification
1. If the text is a different language, need to output "Not tamil"