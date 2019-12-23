import os
import venv
import pkgutil
import warnings
import tempfile
from glob import glob
from importlib import import_module

DEBUG = True
VENV_DIR = './.venv/'
PRINT_INSTALL_ERRORS = True
ALLOW_INSTALL = os.environ.get('BENCHO_ALLOW_INSTALL', False)


if not os.path.exists(VENV_DIR):
    os.mkdir(VENV_DIR)


# Bash commands for installing and checking the solvers
PIP_INSTALL_CMD = "pip install -qq {packages}"
BASH_INSTALL_CMD = "bash install_scripts/{install_script} {env}"
CHECK_PACKAGE_INSTALLED_CMD = (
    "python -c 'import {import_name}' 1>/dev/null 2>&1"
)
CHECK_CMD_INSTALLED_CMD = "type $'{cmd_name}' 1>/dev/null 2>&1"


def _run_in_bash(script):
    """Run a bash script and return its exit code.

    Parameters
    ----------
    script: str
        Script to run

    Return
    ------
    exit_code: int
        Exit code of the script
    """
    # Use a TemporaryFile to make sure this file is cleaned up at
    # the end of this function.
    tmp = tempfile.NamedTemporaryFile(mode="w+")
    with open(tmp.name, 'w') as f:
        f.write(script)

    if DEBUG:
        print(script)

    return os.system(f"bash {tmp.name}")


def _run_bash_in_env(script, env_name=None):
    """Run a script in a given virtual env

    Parameters
    ----------
    script: str
        Script to run
    env_name: str
        Name of the environment to run the script in

    Return
    ------
    exit_code: int
        Exit code of the script
    """
    if env_name is not None:
        env_dir = f"{VENV_DIR}/{env_name}"

        script = f"""
            source {env_dir}/bin/activate
            {script}
        """

    return _run_in_bash(script)


def pip_install_in_env(*packages, env_name=None):
    """Install the packages with pip in the given environment"""
    if env_name is None and not ALLOW_INSTALL:
        raise ValueError("Trying to install solver not in a virtualenv. "
                         "To allow this, set BENCHO_ALLOW_INSTALL=True.")
    cmd = PIP_INSTALL_CMD.format(packages=' '.join(packages))
    exit_code = _run_bash_in_env(cmd, env_name=env_name)
    if exit_code != 0:
        raise RuntimeError(f"Failed to pip install packages {packages}")


def bash_install_in_env(script, env_name=None):
    """Run a bash install script in the given environment"""
    if env_name is None and not ALLOW_INSTALL:
        raise ValueError("Trying to install solver not in a virtualenv. "
                         "To allow this, set BENCHO_ALLOW_INSTALL=True.")
    env = "$VIRTUAL_ENV" if env_name is not None else "$HOME/.local/"
    cmd = BASH_INSTALL_CMD.format(install_script=script, env=env)
    exit_code = _run_bash_in_env(cmd, env_name=env_name)
    if exit_code != 0:
        raise RuntimeError(f"Failed to run script {script}")


def check_import_solver(import_name, env_name=None):
    """Check that a python package is installed in an environment.

    Parameters
    ----------
    import_name : str
        Name of the package that should be installed. This function checks that
        this package can be imported in python.
    env_name : str or None
        Name of the virtual environment to check. If it is None, check in the
        current environment.
    """
    # TODO: if env is None, check directly in the current python interpreter
    check_package_installed_cmd = CHECK_PACKAGE_INSTALLED_CMD.format(
        import_name=import_name)
    return _run_bash_in_env(check_package_installed_cmd,
                            env_name=env_name) == 0


def check_cmd_solver(cmd_name, env_name=None):
    """Check that a cmd is available in an environment.

    Parameters
    ----------
    cmd_name : str
        Name of the cmd that should be installed. This function checks that
        this cmd is available on the path of the environment.
    env_name : str or None
        Name of the virtual environment to check. If it is None, check in the
        current environment.
    """
    check_cmd_installed_cmd = CHECK_CMD_INSTALLED_CMD.format(
        cmd_name=cmd_name)
    return _run_bash_in_env(check_cmd_installed_cmd,
                            env_name=env_name) == 0


def get_all_benchmarks():
    """List all the available benchmarks."""
    benchmark_files = glob("benchmarks/*/bench*.py")
    benchmarks = []
    for benchmark_file in benchmark_files:
        benchmark_name = benchmark_file.split(os.path.sep)[1]
        benchmarks.append(benchmark_name)
    return benchmarks


def check_benchmarks(benchmarks, all_benchmarks):
    unknown_benchmarks = set(benchmarks) - set(all_benchmarks)
    assert len(unknown_benchmarks) == 0, (
        "{} is not a valid benchmark. Should be one of: {}"
        .format(unknown_benchmarks, all_benchmarks)
    )


def get_benchmark_module_name(benchmark):
    return f"benchmarks.{benchmark}"


def load_benchmark_losses(benchmark):
    module_name = get_benchmark_module_name(benchmark)
    module = import_module(module_name)
    return module.loss_function, module.DATASETS


def list_solvers(benchmark):
    submodules = pkgutil.iter_modules([f'benchmarks/{benchmark}/solvers'])
    return [m.name for m in submodules]


def get_all_solvers(benchmark, solver_names=None):

    solver_classes = []
    solvers = list_solvers(benchmark)
    module_name = get_benchmark_module_name(benchmark)
    for s in solvers:
        solver_module_name = f"{module_name}.solvers.{s}"
        solver_module = import_module(solver_module_name)

        # Get the Solver class
        solver_class = solver_module.Solver
        solver_name = solver_class.name.lower()
        if solver_names is None or solver_name in solver_names:
            solver_classes.append(solver_class)

    return solver_classes


def create_venv(env_name, recreate=False):
    """Create a virtual env with name env_name and install basic utilities"""

    env_dir = f"{VENV_DIR}/{env_name}"

    if not os.path.exists(env_dir) or recreate:
        print(f"Creating venv {env_name}:...", end='', flush=True)
        venv.create(env_dir, with_pip=True)
        # Install benchopt as well as packages used as utilities to install
        # other packages.
        pip_install_in_env("numpy", "cython", ".", env_name=env_name)
        print(" done")


def install_solvers(solvers, env_name=None):
    """Install the listed solvers if needed."""

    for solver in solvers:
        solver.install(env_name=env_name)


class safe_import():
    """Do not fail on ImportError and Catch the warnings"""
    def __init__(self):
        self.failed_import = False
        self.record = warnings.catch_warnings(record=True)

    def __enter__(self):
        self.record.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):

        silence_error = False

        # prevent import error from propagating and tag
        if exc_type is not None and issubclass(exc_type, ImportError):
            self.failed_import = True

            if PRINT_INSTALL_ERRORS:
                import traceback
                traceback.print_exc()

            # Prevent the error propagation
            silence_error = True

        self.record.__exit__(exc_type, exc_value, traceback)
        return silence_error