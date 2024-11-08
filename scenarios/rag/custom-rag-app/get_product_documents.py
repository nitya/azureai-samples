# <imports-and-config>
import os
import logging
from opentelemetry import trace
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import ConnectionType
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from config import LOGGING_HANDLER, LOGGING_LEVEL, ASSET_PATH

# use the app telemetry settings to configure logging for this module
logger = logging.getLogger(__name__)
logger.addHandler(LOGGING_HANDLER)
logger.setLevel(LOGGING_LEVEL)

# initialize an open telemetry tracer
tracer = trace.get_tracer(__name__)

# create a project client using environment variables loaded from the .env file
project = AIProjectClient.from_connection_string(
    conn_str=os.environ['AIPROJECT_CONNECTION_STRING'],
    credential=DefaultAzureCredential()
)

# create a vector embeddings client that will be used to generate vector embeddings
chat = project.inference.get_chat_completions_client()
embeddings = project.inference.get_embeddings_client()

# use the project client to get the default search connection
search_connection = project.connections.get_default(
    connection_type=ConnectionType.AZURE_AI_SEARCH,
    with_credentials=True)

# Create a search index client using the search connection
# This client will be used to create and delete search indexes
search_client = SearchClient(
    index_name=os.environ['AISEARCH_INDEX_NAME'],
    endpoint=search_connection.endpoint_url,
    credential=AzureKeyCredential(key=search_connection.key)
)
# </imports-and-config>

# <get-product-documents>
from azure.ai.inference.prompts import PromptTemplate
from azure.search.documents.models import VectorizedQuery

@tracer.start_as_current_span(name="get_product_documents")
def get_product_documents(messages : list, context : dict = {}) -> dict:
    overrides = context.get("overrides", {})
    top = overrides.get("top", 5)

    # generate a search query from the chat messages
    intent_prompty = PromptTemplate.from_prompty(
        os.path.join(ASSET_PATH, "intent_mapping.prompty")
    )

    intent_mapping_response = chat.complete(
        model=os.environ["INTENT_MAPPING_MODEL"],
        messages=intent_prompty.render(conversation=messages),
        **intent_prompty.parameters,
    )

    search_query = intent_mapping_response.choices[0].message.content
    logger.info(f"🧠 Intent mapping: {search_query}")
    
    # generate a vector representation of the search query
    embedding = embeddings.embed(model=os.environ["EMBEDDINGS_MODEL"], input=search_query)
    search_vector = embedding.data[0].embedding

    # search the index for products matching the search query
    vector_query = VectorizedQuery(
        vector=search_vector,
        k_nearest_neighbors=top,
        fields="contentVector")
    
    search_results = search_client.search(
        search_text=search_query,
        vector_queries=[vector_query],
        select=["id", "content", "filepath", "title", "url"])
    
    documents = [{
        "id": result["id"],
        "content": result["content"],
        "filepath": result["filepath"],
        "title": result["title"],
        "url": result["url"],
    } for result in search_results]

    # add results to the provided context
    if "thoughts" not in context:
        context["thoughts"] = []

    # add thoughts and documents to the context object so it can be returned to the caller
    context["thoughts"].append({
        "title": "Generated search query",
        "description": search_query,
    })

    if "grounding_data" not in context:
        context["grounding_data"] = []
    context["grounding_data"].append(documents)

    logger.info(f"📄 {len(documents)} documents retrieved: {documents}")
    return documents
# </get-product-documents>

# <test-get-product-documents>
if __name__ == "__main__":
    import argparse

    # load command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--query", 
        type=str, 
        help="Query to use to search product", 
        default="I need a new tent for 4 people, what would you recommend?"
    )

    args = parser.parse_args()
    query = args.query

    result = get_product_documents(messages=[
        {"role": "user", "content": query}
    ])
# </test-get-product-documents>