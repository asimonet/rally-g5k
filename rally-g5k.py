#!/usr/bin/env python

import traceback
import logging, time, datetime, signal
import pprint, os, sys, math
pp = pprint.PrettyPrinter(indent=4).pprint
from time import sleep
import json
import re
import tempfile

import jinja2

import execo as EX
from string import Template
from execo import configuration
from execo.log import style
from execo.process import ProcessOutputHandler
import execo_g5k as EX5
from execo_g5k.api_utils import get_cluster_site
from execo_engine import Engine, ParamSweeper, logger, sweep, sweep_stats, slugify
#EX.logger.setLevel(logging.ERROR)
#logger.setLevel(logging.ERROR)

#EXCLUDED_ELEMENTS = ['paranoia-4', 'paranoia-7', 'paranoia-8']
EXCLUDED_ELEMENTS = ['sagittaire-69']

# Shortcut
funk = EX5.planning

# Default values
default_job_name = 'Rally'
job_path = "/root/"
RALLY_INSTALL_URL = 'https://raw.githubusercontent.com/openstack/rally/master/install_rally.sh'
DEFAULT_RALLY_GIT = 'https://git.openstack.org/openstack/rally'

# Time to wait before and after running a benchmark (seconds)
idle_time = 30

defaults = {}
defaults['env_user'] = 'ansimonet'
defaults['os-region'] = 'RegionOne'
defaults['os-user-domain'] = 'default'
defaults['os-admin-domain'] = 'default'
defaults['os-project-domain'] = 'default'

class rally_g5k(Engine):

	def __init__(self):
		"""Define options for the experiment"""
		super(rally_g5k, self).__init__()
		self.options_parser.add_option("-k", dest="keep_alive",
				help="Keep the reservation alive.",
				action="store_true")
		self.options_parser.add_option("--job-name", dest="job_name", default=default_job_name,
				help="Name of the existing OAR job or of the job that will be created. " +
				"(default: %s)" % default_job_name)
		self.options_parser.add_option("-f", "--force-deploy", dest="force_deploy", default=False,
				action="store_true",
				help="Deploy the node without checking if it is already deployed. (default: %(defaults)s)")
		self.options_parser.add_option("-v", "--rally-verbose", dest="verbose", default=False,
				action="store_true",
				help="Make Rally produce more insightful output. (default: %(defaults))")


	def run(self):
		"""Perform experiment"""
		logger.detail(self.options)

		# Checking the options
		if len(self.args) < 2:
			self.options_parser.print_help()
			exit(1)

		# Load the configuration file
		try:
			with open(self.args[0]) as config_file:
				self.config = json.load(config_file)
		except:
			logger.error("Error reading configuration file")
			t, value, tb = sys.exc_info()
			print str(t) + " " + str(value)
			exit(3)

		# Put default values
		for key in defaults:
			if not key in self.config['authentication'] or self.config['authentication'][key] == "":
				self.config['authentication'][key] = defaults[key]
				logger.info("Using default value '%s' for '%s'" % (self.config['authentication'][key], key))

			if not 'rally-git' in self.config or self.config['rally-git'] == '':
				self.config['rally-git'] = DEFAULT_RALLY_GIT
				logger.info("Using default Git for Rally: %s " % self.config['rally-git'])

		try:
			self.rally_deployed = False

			# Retrieving the host for the experiment
			self.host = self.get_host()
			
			if self.host is None:
				logger.error("Cannot get host for request")
				exit(1)

			# Deploying the host and Rally
			self.setup_host()
			
			# This will be useful in a bit
			os.mkdir(os.path.join(self.result_dir, 'rally'))
			os.mkdir(os.path.join(self.result_dir, 'energy'))

			experiment = {}
			experiment['start'] = int(time.time())

			# Launch the benchmarks
			benchmarks = {}
			n_benchmarks = len(self.args[1:])
			i_benchmark = 0
			for bench_file in self.args[1:]:
				if not os.path.isfile(bench_file):
					logger.warn("Ignoring %s which is not a file" % bench_file)
					continue

				i_benchmark += 1
				logger.info("[%d/%d] Preparing benchmark %s" % (i_benchmark, n_benchmarks, bench_file))

				# Send the benchmark description file to the host
				EX.Put(self.host, [bench_file], connection_params={'user': 'root'}).run()


				v = ''
				if self.options.verbose:
					v = '-d'
				cmd = "rally %s task start %s" % (v, os.path.basename(bench_file))
				rally_task = EX.Remote(cmd, [self.host], {'user': 'root'})

				logger.info("[%d/%d] Runing benchmark %s" % (i_benchmark, n_benchmarks, bench_file))

				bench_basename = os.path.basename(bench_file)
				benchmarks[bench_basename] = {}

				benchmarks[bench_basename]['idle_start'] = int(time.time())
				time.sleep(idle_time)
				benchmarks[bench_basename]['run_start'] = int(time.time())

				# This is it
				rally_task.run()

				benchmarks[bench_basename]['run_end'] = int(time.time())
				time.sleep(idle_time)
				benchmarks[bench_basename]['idle_end'] = int(time.time())

				if not rally_task.finished_ok:
					logger.error("Error while running benchmark")
					benchmarks[bench_basename]['error'] = ''

					if rally_task.processes[0].stderr is not None:
						logger.error(rally_task.processes[0].stderr)

						# Try to find the reason
						lines = rally_task.processes[0].stdout.splitlines(True)
						for i in range(0, len(lines)):
							if 'Task config is invalid' in lines[i]:
								benchmarks[bench_basename]['error'] += lines[i].strip()

							if 'Reason:' in lines[i]:
								benchmarks[bench_basename]['error'] += lines[i+1].strip()

					continue
				else:
					# Getting the results back
					self._get_logs(bench_basename)

					# Get the energy consumption from the kwapi API
					#self._get_energy(bench_basename, benchmarks[bench_basename]['idle_start'], benchmarks[bench_basename]['idle_end'])

				logger.info('----------------------------------------')
		except Exception as e:
			t, value, tb = sys.exc_info()
			print str(t) + " " + str(value)
			traceback.print_tb(tb)
		finally:
			self.tear_down()

			# Write info about the benchmarks to experiment.json
			if self.rally_deployed:
				out_path = os.path.join(self.result_dir, 'experiment.json')
				experiment['nodes'] = {}
				experiment['nodes']['services'] = self.config['os-services']
				experiment['nodes']['computes'] = self.config['os-computes']
				experiment['end'] = int(time.time())
				experiment['benchmarks'] = benchmarks

				with open(out_path, 'w') as f:
					f.write(json.dumps(experiment, indent=3))

				logger.info("Wrote " + out_path)

		exit()


	def setup_host(self):
		"""Deploy operating setup active data on the service node and
		Hadoop on all"""

		logger.info('Deploying environment %s on %s' % (style.emph(self.config['env_name']), self.host) +
				(' (forced)' if self.options.force_deploy else ''))

		deployment = None
		if 'env_user' not in self.config or self.config['env_user'] == '':
			deployment = EX5.Deployment(hosts=[self.host], env_name=self.config['env_name'])
		else:
			deployment = EX5.Deployment(hosts=[self.host], env_name=self.config['env_name'],
				user=self.config['env_user'])

		deployed_hosts, _ = EX5.deploy(deployment, check_deployed_command=not self.options.force_deploy)

		# Test if rally is installed
		test_p = EX.SshProcess('rally version', self.host, {'user': 'root'})
		test_p.ignore_exit_code = True
		test_p.nolog_exit_code = True
		test_p.run()

		if test_p.exit_code != 0:
			# Install rally
			self._run_or_abort("curl -sO %s" % RALLY_INSTALL_URL, self.host,
				"Could not download Rally install script from %s" % RALLY_INSTALL_URL,
				conn_params={'user': 'root'})

			logger.info("Installing dependencies on deployed host")
			self._run_or_abort('apt-get update && apt-get -y update', self.host,
				'Could not update packages on host',
				conn_params={'user': 'root'})
			
			self._run_or_abort('apt-get -y install python-pip', self.host,
				'Could not install pip on host',
				conn_params={'user': 'root'})
			self._run_or_abort('pip install --upgrade setuptools', self.host,
				'Could not upgrade setuptools',
				conn_params={'user': 'root'})

			logger.info("Installing rally from %s" % style.emph(self.config['rally-git']))
			self._run_or_abort("bash install_rally.sh -y --url %s" %
				self.config['rally-git'], self.host, 'Could not install Rally on host',
				conn_params={'user': 'root'})
		else:
			logger.info("Rally %s is already installed" % test_p.stdout.rstrip())

		# Setup the deployment file
		vars = {
			"controller": self.config['os-services']['controller'],
			"os_region": self.config['authentication']['os-region'],
			"os_username": self.config['authentication']['os-username'],
			"os_password": self.config['authentication']['os-password'],
			"os_tenant": self.config['authentication']['os-tenant'],
			"os_user_domain": self.config['authentication']['os-user-domain'],
			"os_admin_domain": self.config['authentication']['os-admin-domain'],
			"os_project_domain": self.config['authentication']['os-project-domain']
		}
		rally_deployment = self._render_template('templates/deployment_existing.json', vars)
		EX.Put([self.host], [rally_deployment],
			remote_location='deployment_existing.json',
			connection_params={'user': 'root'}).run()

		# Create a Rally deployment
		self._run_or_abort("rally deployment create --filename deployment_existing.json "
				"--name %s" % self.config['deployment_name'], self.host, 'Could not create the Rally deployment',
				conn_params={'user': 'root'})
		self.rally_deployed = True

		logger.info("Rally has been deployed correctly")


	def get_host(self):
		"""Returns the hosts from an existing reservation (if any), or from
		a new reservation"""

		# Look if there is a running job
		self.site = get_cluster_site(self.config['cluster'])
		jobs = EX5.get_current_oar_jobs([self.site])

		self.job_id = None
		for t in jobs:
			if EX5.get_oar_job_info(t[0], self.site)['name'] == self.options.job_name:
				self.job_id = t[0]
				break

		if self.job_id:
			logger.info('Using job %s' % style.emph(self.job_id))
		else:
			logger.info('Making a new reservation')
			self._make_reservation(self.site)
			
		if not self.job_id:
			logger.error("Could not get a reservation for the job")
			exit(6)
		
		EX5.wait_oar_job_start(self.job_id, self.site)

		pp(EX5.get_oar_job_nodes(self.job_id, self.site))
		return EX5.get_oar_job_nodes(self.job_id, self.site)[0]


	def _make_reservation(self, site):
		"""Make a new reservation"""

		elements = {self.config['cluster']: 1}
		logger.info('Finding slot for the experiment '
					'\nrally %s:1',
					style.host(self.config['cluster']).rjust(5))

		planning = funk.get_planning(elements)
		slots = funk.compute_slots(planning, walltime=self.config['walltime'].encode('ascii', 'ignore'), excluded_elements=EXCLUDED_ELEMENTS)

		startdate, enddate, resources = funk.find_free_slot(slots, elements)
		resources = funk.distribute_hosts(resources, elements, EXCLUDED_ELEMENTS)

		if startdate is None:
			logger.error("Sorry, could not find the resources requested.")
			exit(4)

		jobs_specs = funk.get_jobs_specs(resources, name=self.options.job_name, excluded_elements=EXCLUDED_ELEMENTS)

		print jobs_specs
		
		sub, site = jobs_specs[0]
		sub.additional_options = "-t deploy"
		sub.reservation_date = startdate
		sub.walltime = self.config['walltime'].encode('ascii', 'ignore')
		sub.name = self.options.job_name
		
		if 'testing' in EX5.get_cluster_attributes(self.config['cluster'])['queues']:
			sub.queue = 'testing'
		
		jobs = EX5.oarsub([(sub, site)])
		self.job_id = jobs[0][0]
		logger.info('Job %s will start at %s', style.emph(self.job_id),
				style.log_header(EX.time_utils.format_date(startdate)))


	def tear_down(self):
		# Destroy the Rally deployment
		try:
			if self.rally_deployed:
				logger.info("Destroying Rally deployment " + self.config['deployment_name'])
				self._run_or_abort('rally deployment destroy %s' % self.config['deployment_name'],
						self.host,
						'Could not destroy the Rally deployment. This will likely '
						'cause errors when the node is used again.',
						False, {'user': 'root'})
		except AttributeError:
			pass # self.host has not been defined yet, and that's ok

		# Kill the job
		try:
			if not self.options.keep_alive and self.job_id:
				logger.info("Killing job " + str(self.job_id))
				EX5.oardel([(self.job_id, self.site)])
		except AttributeError:
			pass # self.job_id has not been defined either, and that's ok too

	def _get_logs(self, bench_file):
		# Generating the HTML file
		logger.info("Getting the results into " + self.result_dir)
		html_file = os.path.splitext(bench_file)[0] + '.html'
		dest = os.path.join(self.result_dir, 'rally', html_file)
		result = EX.Remote("rally task report --out=" + html_file, [self.host], {'user': 'root'})
		result.run()

		if result.processes[0].exit_code != 0:
			logger.error("Could not generate the HTML result file")

			if result.processes[0].stderr:
				logger.error(result.processes[0].stderr)
		else:
			# Downloading the HTML file

			EX.Get(self.host, [html_file], local_location=dest, connection_params={'user': 'root'}).run()
			logger.info("Wrote " + dest)

		# Get the metrics from Rally
		result = EX.Remote("rally task results", [self.host], {'user': 'root'})
		metrics_file = os.path.join(self.result_dir, 'rally', os.path.splitext(bench_file)[0] + '.json')
		result.run()

		if result.processes[0].exit_code != 0:
			logger.error("Could not get the metrics back")

			if result.processes[0].stderr:
				logger.error(result.processes[0].stderr)
		else:
			# The json is on the standard output of the process
			with open(metrics_file, 'w') as f:
				f.write(result.processes[0].stdout)
			logger.info("Wrote " + metrics_file)
	
	def _get_energy(self, bench_file, start, end):
		"""Get the power consumption metrics for Kwapi
		This call writes a single JSON file with the metrics of all the nodes."""

		# TODO get metrics from service nodes
		nodes = []
		for n in self.config['os-computes']:
			try:
				nodes.append(re.search(r"(\w+\-\d+)\-\w+\-\d+", n).group(1))
			except AttributeError:
				nodes.append(re.search(r"(\w+\-\d+)\.\w+\.grid5000\.fr", n).group(1))

		for role, n in self.config['os-services'].items():
			try:
				nodes.append(re.search(r"(\w+\-\d+)\-\w+\-\d+", n).group(1))
			except AttributeError:
				nodes.append(re.search(r"(\w+\-\d+)\.\w+\.grid5000\.fr", n).group(1))

		url = "/sites/%s/metrics/power/timeseries/?from=%d&to=%d&only=%s" % (self.site, start, end, ','.join(nodes))

		# This call to the API must be authenticated
		i = 0
		while True:
			data = EX5.get_resource_attributes(url)
			i = i + 1
			timestamps = data['items'][0]['timestamps']
			time.sleep(0.2)

			if len(timestamps) > 0:
				break

		logger.info("%d attempt(s) for %s" % (i, url))

		# Write the metrics from the API
		energy_path = os.path.join(self.result_dir, 'energy', os.path.splitext(bench_file)[0] + '.json')
		json_data = json.dumps(data, indent=3)

		with open(energy_path, 'w') as f:
			f.write(json_data)

		logger.info("Wrote " + f.name)

	def _render_template(self, template_path, vars):
		template_loader = jinja2.FileSystemLoader(searchpath='.')
		template_env = jinja2.Environment(loader=template_loader)
		template = template_env.get_template(template_path)
		
		f = tempfile.NamedTemporaryFile('w', delete=False)
		f.write(template.render(vars))
		f.close()
		
		return f.name

	def _run_or_abort(self, cmd, host, error_message, tear_down=True, conn_params=None):
		"""Attempt to run a command on the given host. If the command fails,
		error_message and the process error output will be printed.

		In addition, if tear_down is True, the tear_down() method will be
		called and the process will exit with return code 1"""

		if conn_params:
			p = EX.SshProcess(cmd, host, conn_params)
		else:
			p = EX.SshProcess(cmd, host)
		p.run()

		if p.exit_code != 0:
			logger.warn(error_message)

			if p.stderr is not None:
				logger.warn(p.stderr)

			logger.info(' '.join(p.cmd))

			if tear_down:
				self.tear_down()
				exit(1)

def sizeof_fmt(num, suffix='B'):
	for unit in ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z']:
		if abs(num) < 1000.0:
			return "%3.1f%s%s" % (num, unit, suffix)
		num /= 1000.0
	return "%.1f%s%s" % (num, 'Y', suffix)


def timestamp2str(timestamp):
	return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def prediction(timestamp):
	start = timestamp2str(timestamp)
	rally_g5k._log("Waiting for job to start (prediction: {0})".format(start), False)


class FileOutputHandler(ProcessOutputHandler):
	__file = None

	def __init__(self, path):
		super(ProcessOutputHandler, self).__init__()
		self.__file = open(path, 'a')

	def __del__(self):
		self.__file.flush()
		self.__file.close()

	def read(self, process, string, eof=False, error=False):
		self.__file.write(string)
		self.__file.flush()

	def read_line(self, process, string, eof=False, error=False):
		self.__file.write(time.localtime().strftime("[%d-%m-%y %H:%M:%S"))
		self.__file.write(' ')
		self.__file.write(string)
		self.__file.flush()


###################
# Main
###################
if __name__ == "__main__":
	print("Execo version: " + EX._version.__version__)
	engine = rally_g5k()
	engine.start()
   
