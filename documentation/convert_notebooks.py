import os
from datetime import datetime
import yaml
import warnings
from nbconvert import MarkdownExporter
from yaml.loader import FullLoader
import requests
import re
from traitlets.config import Config
from nbconvert.preprocessors import TagRemovePreprocessor


def download_image(image_url, image_path):
    response = requests.get(image_url)
    image_content = response.content
    with open(image_path, "wb") as f:
        f.write(image_content)


def rename(file_name):
    file_name = file_name.replace("_", "-").lower()
    return file_name.replace(" ", "-").lower()


def process_img(nb_body, folder_name, output_dir, media_dir):
    """
    handle Azure doc validation rule (Suggestion: external-image, Warning: alt-text-missing)
    scan text and find external image link, download the image and store in the img folder
    replace image link with img path
    """
    image_tags = re.finditer(r"<img.*?>|<image.*?>", nb_body)
    process_nb_body = []
    prev = 0
    for match in image_tags:
        start_index = match.start()
        end_index = match.end()
        content = nb_body[prev:start_index]
        process_nb_body.append(content)
        url = re.search(
            r"<(?:img|image).*?src=\"(.*?)\".*?>", nb_body[start_index:end_index]
        ).group(1)
        file_name = url.split("/")[-1]
        file_dir = "/".join([output_dir, media_dir, folder_name])
        make_dir(file_dir)
        img_azure_doc_path = "/".join([folder_name, rename(file_name)])
        md_img_input_path = "/".join([media_dir, img_azure_doc_path])
        file_path = "/".join([output_dir, media_dir, img_azure_doc_path])
        download_image(url, file_path)
        alt_text = file_name.split(".")[0]
        md_img_path = ':::image source="{img_path}" alt-text="picture {alt_text}":::'.format(
            img_path=md_img_input_path, alt_text=alt_text
        )
        process_nb_body.append(md_img_path)
        prev = end_index
    process_nb_body.append(nb_body[prev:])
    return "".join(process_nb_body)


def make_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def convert_notebook_to_md(input_file):
    """
    convert notebook (.ipynb) file to markdown format
    remove cell with tag hide-synapse-internal
    """
    c = Config()
    c.TagRemovePreprocessor.remove_cell_tags = ("hide-synapse-internal",)
    c.TagRemovePreprocessor.enabled = True
    c.MarkdownExporter.preprocessors = ["nbconvert.preprocessors.TagRemovePreprocessor"]
    exporter = MarkdownExporter(config=c)
    nb_body, _ = exporter.from_filename(input_file)
    return nb_body

def remove_replace_content(text):
    replace_mapping = {"from synapse.ml.core.platform import materializing_display as display":"",
                        "https://docs.microsoft.com":"", "https://learn.microsoft.com":""}
    for ori, new in replace_mapping.items():
        text = text.replace(ori, new)
    return text


def replace_doc_link_absolute(text, replace_mapping):
    """
    handle Azure doc validation rule (Suggestion: docs-link-absolute)
    given text and replace_dict, replace the content

    https://review.learn.microsoft.com/en-us/help/platform/validation-ref/docs-link-absolute?branch=main
    """
    # TODO: automate this and remove hard coded path in azure_doc_structure.yml
    for absolute_link, relative_link in replace_mapping.items():
        text = text.replace(absolute_link, relative_link)
    return text


def header1_to_header2(input_string):
    """
    handle Azure doc validation rule (Warning) multiple-h1s
    find the first line start with '#' and turn it to '##'

    https://review.learn.microsoft.com/help/platform/validation-ref/multiple-h1s?branch=main
    """
    if input_string.startswith("#") and not input_string.startswith("##"):
        return "#" + input_string
    else:
        return input_string


class Document:
    def __init__(self, filename, content):
        self.filename = filename
        self.content = content
        self.input_path = content["input_path"]
        self.output_dir = content["output_dir"]
        self.media_dir = content["media_dir"]
        try:
            self.replace_mapping = content["replace_mapping"]
        except KeyError:
            self.replace_mapping = {}

    def generate_metadata(self):
        """
        take a file and the authors name, generate metadata
        metadata requirements: https://learn.microsoft.com/en-us/contribute/metadata

        Can not use notebook authors as Azure Doc authors.
        Azure Doc require MS authors and contributors need to make content contributions through the private repository
        so the content can be staged and validated by the current validation rules. (Jan 4th, 2023)
        """
        metadata = self.content["metadata"]
        # ensure required metadata info
        for required_metadata in {
            "author",
            "description",
            "ms.author",
            "ms.topic",
            "title",
        }:
            if required_metadata not in metadata:
                raise ValueError(
                    "{required_metadata} is required metadata by azure doc, please add it to yml file".format(
                        required_metadata=required_metadata
                    )
                )
        # Validation Rules are different between Synapse-internal and Azure Doc
        # if ("ms.service" not in metadata) and ("ms.prod" not in metadata):
        #     raise ValueError(
        #         "either ms.service or ms.prod must be in metadata, please add it to yml file"
        #     )
        # generate final metadata
        generated_metadata = "---\n"
        for k, v in metadata.items():
            generated_metadata += "{k}: {v}\n".format(k=k, v=v)
        if "ms.date" not in metadata:
            update_date = datetime.today().strftime("%m/%d/%Y")
            generated_metadata += "ms.date: {update_date}\n".format(
                update_date=update_date
            )
        else:
            warnings.warn(
                "ms.date is set in yml file, the date won't be automatically updated. if you want the date to be updated automatically, remove ms.date from yml file"
            )

        generated_metadata += "---\n"
        return generated_metadata

    def combine_documentation(self, generated_metadata, body):
        """
        combine documentation with metadata, and platform specified info
        """
        if "front" in content:
            with open(content["front"], "r") as f:
                front = f.read()
        else:
            front = ""

        if "end" in content:
            with open(content["end"], "r") as f:
                end = f.read()
        else:
            end = ""

        if front:
            body = header1_to_header2(body)
        generated_doc = "".join([generated_metadata, front, body, end])

        generated_doc = remove_replace_content(generated_doc)

        if self.replace_mapping:
            generated_doc = replace_doc_link_absolute(
                generated_doc, self.replace_mapping
            )
        return generated_doc

    def run(self):
        body = convert_notebook_to_md(self.input_path)
        body = process_img(body, self.filename, self.output_dir, self.media_dir)
        generated_metadata = self.generate_metadata()
        combined_documentation = self.combine_documentation(generated_metadata, body)

        if not os.path.exists(self.output_dir):
            os.mkdir(self.output_dir)
        output_file = self.output_dir + "/" + self.filename + ".md"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(combined_documentation)


if __name__ == "__main__":
    with open("fabric_doc_structure.yml", "r") as f:
        structure = yaml.load(f, Loader=FullLoader)
    for doc_name, content in structure.items():
        print(doc_name)
        if content["active"]:  # TODO: adding try except, default to active
            doc = Document(doc_name, content)
            doc.run()
