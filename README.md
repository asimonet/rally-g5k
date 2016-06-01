# Rally-G5K
## Synopsis
Rally-G5K allows to simply deploy an OpenStack Rally instance on Grid'5000 and
run scenarios on a OpenStack also deployed on Grid'5000. Rally-G5K does not
deploy OpenStack and requires the credentials to an OpenStack deployment to be
provided.

The script performs the following operations:

1. Make a single-node Grid'5000 reservation and wait for the job to start;
2. Deploy the node, install Rally dependencies and Rally itself;
3. Make a Rally deployment;
4. Run the given Rally scenarios and get the resulting JSON and HTML reports;
5. Destroy the Rally deployment;
6. Kill the Grid'5000 job.

## Requirements
Rally-G5K requires pip, [Execo](http://execo.gforge.inria.fr/) and [Jinja](http://jinja.pocoo.org/).

## Installation
To install Rally, first clone the repository:
```
https://github.com/asimonet/rally-g5k.git
```

Then install the dependencies:
```
pip install -r requirements.txt
```

## Some nodes
As this script was made with scientific experimentation in minds, it does a
certain number of things that you may not want:
* a pause is made before and after running each scenario. This is intended to
allow to measure a baseline on various metrics of the node (e.g. the energy).
The lenght of this pause is set by the `idle_time` constant in `raddy-g5k.py`.
* energy metrics are downloaded from the Grid'5000 API. Obviously, you may not
want to do that by commenting the call to `self._get_energy`.
* downloading the energy metrics from the API is the only reason for the
presence of the `storage` and `network` keys in the `os-services` configuration.
They are bond to disappear in the future.

## Running
To run Rally, you need to provide a configuration file in the JSON format. This
file must include information about the Grid'5000 job, the authentification
credentials to connect to the OpenStack cloud and the addresses of some machines
in the cluster. `config.json.sample` is given as an example.

Rally will work with a production environment (as given in the sample
configuration file), but you can use a private or shared environment. However,
in this case, you must provide a `env-user` configuration key.

By default, the script installs the official OpenStack Rally. If you want to
modify some of the scenarios, you will need to clone this repository and
indicate Rally-G5K to use yours. This is done with the `rally-git` configuration
key.

Then, to run Rally with two scenarios:
```
./rally-g5k config.json boot-and-delete.json boot-and-migrate.json
```

The scenarios can be taken from the [Rally repository](https://github.com/openstack/rally/tree/master/samples/tasks/scenarios/)
but only JSON-formatted scenarios are supported at this time. As many scenarios
as necessary can be given as command-line arguments.

After the execution, a new directory will contain Rally report files (in JSON
and HTML) in a `rally` sub-directory, along with a `experiment.json` document
that sums up some information about the scenarios. In particular, for each
scenario will be included 4 timestamps. `idle_start` is the time at which
the pause begins, `run_start` is the start at which the actual scenario
task begins, `run_end` is the time at which the task ended and `idle_end`
is the time at which the post-pause ended. Errors and failures are also logged
here for each scenario.

## License
This code is released under the GNU General Public License. It is also worth
mentionning here that it is being developped by [me](http://www.anthony-simonet.fr)
as part of my work at [Inria](http://www.inria.fr).
