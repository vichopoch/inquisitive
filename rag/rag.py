from typing import Callable
import openai
import os
from weaviate.util import generate_uuid5
import weaviate
from . import db
from . import prompts
from .utils import truncate_text
import logging


logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
logger = logging.getLogger(__name__)


GPT_MODELS = ["gpt-3.5-turbo", "gpt-3.5-turbo-16k", "gpt-4", "gpt-4-32k"]
ALL_MODELS = GPT_MODELS

openai.api_key = os.environ["OPENAI_APIKEY"]


class RAGTask:
    """
    A class representing a task to be handled by the RAG system.

    Attributes:
        task_prompt_builder (str): A function to build the task prompt using the source text.
        generated_text (str): The text generated as output for the task.
    """
    def __init__(self, task_prompt_builder: Callable[[str], str], client: weaviate.Client = None):
        if not callable(task_prompt_builder):
            raise ValueError('task_prompt_builder must be a callable function')
        self.task_prompt_builder = task_prompt_builder
        self.generated_text = None
        if client is None:
            self.client = db.initialize()
        else:
            self.client = client

    def get_output(self, source_text: str, model_name: str = "gpt-3.5-turbo", overwrite: bool = False) -> str:
        """
        Get the output for the task, either by generating it or fetching from Weaviate.
        :param source_text: The source text based on which the task is created.
        :param model_name: The name of the model to use for generating output.
        :param overwrite: Whether to overwrite the output if it already exists in Weaviate.
        :return: The generated output.
        """

        task_prompt = self.task_prompt_builder(source_text)
        uuid = generate_uuid5(task_prompt)

        # Check if the output can be fetched from Weaviate using the UUID
        fetched_object = db.load_generated_text(self.client, uuid)
        if fetched_object is not None:
            logger.info(f"Found {uuid} in Weaviate")
            if overwrite:
                logger.info(f"Overwrite is true. Deleting object {uuid} in Weaviate")
                self.client.data_object.delete(uuid, class_name=db.OUTPUT_COLLECTION)
                logger.info(f"Deleted {uuid} in Weaviate")
            else:
                return fetched_object
        else:
            logger.warning(f"Could not find {uuid} in Weaviate")

            # Check if there are any similar objects in Weaviate based on vector similarity
            similar_objects = db.find_similar_objects(self.client, task_prompt, similarity_theshold=0.95)
            if similar_objects:
                logger.info(f"Found similar object(s) in Weaviate with similarity above threshold")
                logger.info(f"UUID: {similar_objects[0]['_additional']['id']}")
                logger.info(f"Distance: {similar_objects[0]['_additional']['distance']}")
                # Load the most similar object
                most_similar_object = similar_objects[0]
                return most_similar_object["generated_text"]

        # Generate output using the specified model and save it to Weaviate
        logger.info(f"Generating output for {truncate_text(task_prompt)}")
        generated_text = call_llm(task_prompt, model_name=model_name)
        uuid = db.save_generated_text(self.client, task_prompt, generated_text, uuid)
        logger.info(f"Saved {uuid} to Weaviate")
        return generated_text


def call_llm(prompt: str, model_name: str = "gpt-3.5-turbo") -> str:
    """
    Call the language model with a specific prompt and model name.
    :param prompt: The prompt to be used.
    :param model_name: The name of the model to be used.
    :return: The output from the language model.
    """
    logger.info(f"Calling {model_name} with prompt: {truncate_text(prompt)}")
    if model_name not in ALL_MODELS:
        raise ValueError(f"Model name {model_name} not recognised")

    if "gpt" in model_name:
        return call_chatgpt(prompt, model_name)
    else:
        raise ValueError(f"No function exists to handle for model {model_name}")


def call_chatgpt(prompt: str, model_name: str = "gpt-3.5-turbo") -> str:
    """
    Call the ChatGPT model with a specific prompt.
    :param prompt: The prompt to be used.
    :param model_name: The name of the model to be used.
    :return:
    """

    completion = openai.ChatCompletion.create(
        model=model_name,
        messages=[
            prompts.SYSTEM_PROMPTS["Default"],
            {
                "role": "user",
                "content": prompt
            }
        ]
    )
    return completion.choices[0].message["content"]