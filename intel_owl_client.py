import argparse
import hashlib
import logging
import os
import requests
import time
from domain_checkers import Checkers
from pprint import pprint

from pyintelowl.pyintelowl import (
    IntelOwl,
    IntelOwlClientException,
    get_observable_classification,
)


def intel_owl_client():

    parser = argparse.ArgumentParser(description="Intel Owl classic client")
    parser.add_argument(
        "-sc",
        "--show-colors",
        action="store_true",
        default=False,
        help="Show colorful and more user-friendly results."
        " By default JSON raw results are shown",
    )
    parser.add_argument(
        "-k",
        "--api_key",
        help="API key to authenticate against a IntelOwl instance",
    )
    parser.add_argument(
        "-c", "--certificate", default=False, help="path to Intel Owl certificate"
    )
    parser.add_argument("-i", "--instance", required=True, help="your instance URL")
    parser.add_argument(
        "-d", "--debug", action="store_true", default=False, help="debug mode"
    )
    parser.add_argument("-l", "--log-to-file", help="log to specified file")
    parser.add_argument(
        "-gc",
        "--get-configuration",
        action="store_true",
        default=False,
        help="get analyzers configuration only",
    )
    parser.add_argument(
        "-a", "--analyzers-list", action="append", help="list of analyzers to launch"
    )
    parser.add_argument(
        "-aa",
        "--run-all-available-analyzers",
        action="store_true",
        default=False,
        help="run all available and compatible analyzers",
    )
    parser.add_argument(
        "-fp",
        "--force-privacy",
        action="store_true",
        default=False,
        help="disable analyzers that could impact privacy",
    )
    parser.add_argument(
        "-p",
        "--private-job",
        action="store_true",
        default=False,
        help="Limit view permissions to my group",
    )
    parser.add_argument(
        "-e",
        "--disable-external-analyzers",
        action="store_true",
        default=False,
        help="disable analyzers that use external services",
    )
    parser.add_argument(
        "-r",
        "--check-reported-analysis-too",
        action="store_true",
        default=False,
        help="check reported analysis too, not only 'running' ones",
    )
    parser.add_argument(
        "-s",
        "--skip-check-analysis-availability",
        action="store_true",
        default=False,
        help="skip check analysis availability",
    )

    subparsers = parser.add_subparsers(help="choose type of analysis", dest="command")
    parser_sample = subparsers.add_parser("file", help="File analysis")
    parser_observable = subparsers.add_parser("observable", help="Observables analysis")

    parser_sample.add_argument("-f", "--file", required=True, help="file to analyze")
    parser_observable.add_argument(
        "-v", "--value", required=True, help="observable to analyze"
    )

    args = parser.parse_args()

    if not args.get_configuration:
        if not args.analyzers_list and not args.run_all_available_analyzers:
            print(
                "you must specify at least an analyzer (-a) or"
                " every available analyzer (--aa)"
            )
            exit(2)
        if args.analyzers_list and args.run_all_available_analyzers:
            print("you must specify either --aa or -a, not both")
            exit(3)

    logger = get_logger(args.debug, args.log_to_file)

    _pyintelowl_logic(args, logger)


def _pyintelowl_logic(args, logger):
    md5 = None
    results = []
    elapsed_time = None
    get_configuration_only = False
    try:
        filename = None
        binary = None
        if args.command == "file":
            if not os.path.exists(args.file):
                raise IntelOwlClientException(f"{args.file} does not exists")
            with open(args.file, "rb") as f:
                binary = f.read()
                filename = os.path.basename(f.name)
            md5 = hashlib.md5(binary).hexdigest()
        elif args.command == "observable":
            args.value = args.value.lower()
            md5 = hashlib.md5(args.value.encode("utf-8")).hexdigest()
        elif args.get_configuration:
            get_configuration_only = True
        else:
            raise IntelOwlClientException(
                "you must specify the type of the analysis: [observable, file]"
            )

        pyintelowl_client = IntelOwl(
            args.api_key,
            args.certificate,
            args.instance,
            args.debug,
        )

        if get_configuration_only:
            api_request_result = pyintelowl_client.get_analyzer_configs()
            errors = api_request_result.get("errors", [])
            if errors:
                logger.error(f"API get_analyzer_configs failed. Errors: {errors}")
            analyzers_config = api_request_result.get("answer", {})
            logger.info(f"extracted analyzer_configuration: {analyzers_config}")
            pprint(analyzers_config)
            exit(0)

        analysis_available = False
        if not args.skip_check_analysis_availability:
            job_id_to_get = None
            # first step: ask analysis availability
            logger.info(
                f"[STARTED] request ask_analysis_availability for md5: {md5},"
                f" analyzers: {args.analyzers_list}"
            )

            api_request_result = pyintelowl_client.ask_analysis_availability(
                md5,
                args.analyzers_list,
                args.run_all_available_analyzers,
                args.check_reported_analysis_too,
            )
            errors = api_request_result.get("errors", [])
            if errors:
                raise IntelOwlClientException(
                    f"API ask_analysis_availability failed. Errors: {errors}"
                )
            answer = api_request_result.get("answer", {})
            status = answer.get("status", None)
            if not status:
                raise IntelOwlClientException(
                    "API ask_analysis_availability gave result without status!?!?"
                    f" Answer: {answer}"
                )
            if status != "not_available":
                analysis_available = True
                job_id_to_get = answer.get("job_id", "")
                if job_id_to_get:
                    logger.info(
                        f"[INFO] already existing Job(#{job_id_to_get}, md5: {md5},"
                        f" status: {status}) with analyzers: {args.analyzers_list}"
                    )
                else:
                    raise IntelOwlClientException(
                        "API ask_analysis_availability gave result without job_id!?!?"
                        f" Answer:{answer}"
                    )

        # second step: in case there are no analysis available, start a new analysis
        if not analysis_available:

            if args.command == "file":
                api_request_result = pyintelowl_client.send_file_analysis_request(
                    md5,
                    args.analyzers_list,
                    filename,
                    binary,
                    args.force_privacy,
                    args.private_job,
                    args.disable_external_analyzers,
                    args.run_all_available_analyzers,
                )
            elif args.command == "observable":
                api_request_result = pyintelowl_client.send_observable_analysis_request(
                    md5,
                    args.analyzers_list,
                    args.value,
                    args.force_privacy,
                    args.private_job,
                    args.disable_external_analyzers,
                    args.run_all_available_analyzers,
                )
            else:
                raise NotImplementedError()

            # both cases share the same logic for the management of
            # the result retrieved from the API
            errors = api_request_result.get("errors", [])
            if errors:
                raise IntelOwlClientException(
                    f"API send_analysis_request failed. Errors: {errors}"
                )
            answer = api_request_result.get("answer", {})
            logger.info(f"[INFO] md5: {md5} received response from intel_owl: {answer}")
            status = answer.get("status", None)
            if not status:
                raise IntelOwlClientException(
                    "API send_analysis_request gave result without status!?"
                    f" Answer:{answer}"
                )
            if status != "accepted":
                raise IntelOwlClientException(
                    f"API send_analysis_request gave unexpected result status: {status}"
                )
            job_id_to_get = answer.get("job_id", None)
            analyzers_running = answer.get("analyzers_running", None)
            warnings = answer.get("warnings", [])
            if job_id_to_get:
                logger.info(
                    f"[STARTED] Job(#{job_id_to_get}, md5: {md5}, status: {status})"
                    f" -> analyzers: {analyzers_running}. Warnings: {warnings}"
                )
            else:
                raise IntelOwlClientException(
                    f"API send_analysis_request gave result without job_id!?!?"
                    f"answer:{answer}"
                )

        # third step: at this moment we must have a job_id to check for results
        polling_max_tries = 60 * 20
        polling_interval = 1
        logger.info("[STARTED] polling...")
        for chance in range(polling_max_tries):
            time.sleep(polling_interval)
            api_request_result = pyintelowl_client.ask_analysis_result(job_id_to_get)
            errors = api_request_result.get("errors", [])
            if errors:
                raise IntelOwlClientException(
                    f"API ask_analysis_result failed. Errors: {errors}"
                )
            answer = api_request_result.get("answer", {})
            status = answer.get("status", None)
            if not status:
                raise IntelOwlClientException(
                    f"API ask_analysis_result gave result without status!?!?"
                    f" job_id:{job_id_to_get} answer:{answer}"
                )
            if status in ["invalid_id", "not_available"]:
                raise IntelOwlClientException(
                    f"API send_analysis_request gave status {status}"
                )
            if status == "running":
                continue
            if status == "pending":
                logger.warning(
                    f"API ask_analysis_result check job in status 'pending'."
                    f" Maybe it is stuck"
                    f"job_id:{job_id_to_get} md5:{md5}"
                    f" analyzer_list:{args.analyzers_list}"
                )
            elif status in ["reported_without_fails", "reported_with_fails", "failed"]:
                logger.info(
                    f"[FINISHED] Job(#{job_id_to_get}, md5: {md5}, status: {status})"
                    f" -> analyzer_list:{args.analyzers_list}"
                )
                results = answer.get("results", [])
                elapsed_time = answer.get("elapsed_time_in_seconds", [])
                break
        if not results:
            raise IntelOwlClientException(
                f"[ENDED] Reached polling timeout without results."
                f" Job_id: {job_id_to_get}"
            )

    except IntelOwlClientException as e:
        logger.error(f"Error: {e} md5: {md5}")
    except requests.exceptions.HTTPError as e:
        logger.exception(e)
    except Exception as e:
        logger.exception(e)

    logger.info(f"Elapsed time: {elapsed_time}")
    logger.info("Results:")
    if args.show_colors:
        checkers = Checkers(results, args.value)
        observable = get_observable_classification(args.value)
        if "domain" in observable:
            checkers.check_domain()
        elif "hash" in observable:
            checkers.check_hash()
        elif "url" in observable:
            checkers.check_url()
        else:
            checkers.check_ip()
    else:
        pprint(results)


def get_logger(debug_mode, log_to_file):
    if debug_mode:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO
    if log_to_file:
        handler = logging.FileHandler(log_to_file)
    else:
        handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s - %(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)
    logger.addHandler(handler)
    return logger


if __name__ == "__main__":
    intel_owl_client()
