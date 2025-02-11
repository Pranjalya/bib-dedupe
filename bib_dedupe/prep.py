#! /usr/bin/env python
"""Preparation for dedupe"""
import re
import typing
import urllib.parse

import colrev.record
import numpy as np
import pandas as pd
from colrev.constants import ENTRYTYPES
from colrev.constants import Fields
from number_parser import parse

# Note: for re.sub, "van der" must be checked before "van"
NAME_PREFIXES_LOWER = [
    "van der",
    "van",
    "von der",
    "von",
    "vom",
    "le",
    "den",
    "der",
    "ter",
    "de",
    "da",
    "di",
]


def get_records_for_dedupe(records_df: pd.DataFrame) -> pd.DataFrame:
    """Prepare records for dedupe"""

    if 0 == records_df.shape[0]:
        return {}

    assert all(
        f in records_df.columns
        for f in [Fields.ENTRYTYPE, Fields.TITLE, Fields.AUTHOR, Fields.YEAR]
    )

    if Fields.STATUS not in records_df:
        records_df[Fields.STATUS] = colrev.record.RecordState.md_processed
        print("setting missing status")

    records_df = records_df[
        ~(
            records_df[Fields.STATUS].isin(
                [
                    colrev.record.RecordState.md_imported,
                    colrev.record.RecordState.md_needs_manual_preparation,
                ]
            )
        )
    ]

    optional_fields = [
        Fields.JOURNAL,
        Fields.BOOKTITLE,
        Fields.SERIES,
        Fields.VOLUME,
        Fields.NUMBER,
        Fields.PAGES,
        Fields.ABSTRACT,
        Fields.ISBN,
        Fields.DOI,
    ]
    for optional_field in optional_fields:
        if optional_field not in records_df:
            records_df[optional_field] = ""

    if Fields.ORIGIN in records_df:
        records_df[Fields.ORIGIN] = join_origin(records_df[Fields.ORIGIN].values)

    records_df.drop(
        labels=list(
            records_df.columns.difference(
                [
                    Fields.ID,
                    Fields.ENTRYTYPE,
                    Fields.AUTHOR,
                    Fields.TITLE,
                    Fields.YEAR,
                    Fields.JOURNAL,
                    Fields.CONTAINER_TITLE,
                    Fields.BOOKTITLE,
                    Fields.VOLUME,
                    Fields.NUMBER,
                    Fields.PAGES,
                    Fields.ORIGIN,
                    Fields.STATUS,
                    Fields.ABSTRACT,
                    Fields.URL,
                    Fields.ISBN,
                    Fields.DOI,
                ]
            )
        ),
        axis=1,
        inplace=True,
    )

    records_df[
        [
            Fields.STATUS,
            Fields.AUTHOR,
            Fields.TITLE,
            Fields.JOURNAL,
            Fields.YEAR,
            Fields.VOLUME,
            Fields.NUMBER,
            Fields.PAGES,
            Fields.ABSTRACT,
            Fields.DOI,
            Fields.ISBN,
        ]
    ] = records_df[
        [
            Fields.STATUS,
            Fields.AUTHOR,
            Fields.TITLE,
            Fields.JOURNAL,
            Fields.YEAR,
            Fields.VOLUME,
            Fields.NUMBER,
            Fields.PAGES,
            Fields.ABSTRACT,
            Fields.DOI,
            Fields.ISBN,
        ]
    ].astype(
        str
    )

    records_df.replace(
        to_replace={
            "UNKNOWN": "",
            "&amp;": "and",
            " & ": " and ",
            " + ": " and ",
            "\n": " ",
        },
        inplace=True,
    )

    records_df[Fields.AUTHOR] = prep_authors(records_df[Fields.AUTHOR].values)

    records_df["author_full"] = records_df[Fields.AUTHOR]

    records_df[Fields.AUTHOR] = select_authors(records_df[Fields.AUTHOR].values)

    set_container_title(records_df)

    records_df[Fields.CONTAINER_TITLE] = prep_container_title(
        records_df[Fields.CONTAINER_TITLE].values
    )
    records_df[Fields.CONTAINER_TITLE] = get_abbrev_container_title(
        records_df[Fields.CONTAINER_TITLE].values
    )
    records_df[Fields.TITLE] = prep_title(records_df[Fields.TITLE].values)
    records_df[Fields.YEAR] = prep_year(records_df[Fields.YEAR].values)
    records_df[Fields.VOLUME] = prep_volume(records_df[Fields.VOLUME].values)
    records_df[Fields.NUMBER] = prep_number(records_df[Fields.NUMBER].values)
    records_df[Fields.PAGES] = prep_pages(records_df[Fields.PAGES].values)
    records_df[Fields.ABSTRACT] = prep_abstract(records_df[Fields.ABSTRACT].values)
    records_df[Fields.DOI] = prep_doi(records_df[Fields.DOI].values)
    records_df[Fields.ISBN] = prep_isbn(records_df[Fields.ISBN].values)

    records_df = records_df.replace("nan", "")

    if Fields.BOOKTITLE in records_df.columns:
        records_df = records_df.drop(columns=[Fields.BOOKTITLE])
    if Fields.JOURNAL in records_df.columns:
        records_df = records_df.drop(columns=[Fields.JOURNAL])

    # For blocking:
    records_df["first_author"] = records_df[Fields.AUTHOR].str.split().str[0]
    records_df["short_container_title"] = get_short_container_title(
        records_df[Fields.CONTAINER_TITLE].values
    )

    return records_df


def join_origin(origins_array: np.array) -> np.array:
    # def join_o(origins: List[str]) -> str:
    #     return ";".join(origins)
    return np.array([";".join(origins) for origins in origins_array])


def set_container_title(records_df: pd.DataFrame) -> None:
    # Set container title
    records_df.loc[
        records_df[Fields.ENTRYTYPE] == ENTRYTYPES.ARTICLE, Fields.CONTAINER_TITLE
    ] = records_df[Fields.JOURNAL]
    records_df.loc[
        records_df[Fields.ENTRYTYPE].isin(
            [ENTRYTYPES.INPROCEEDINGS, ENTRYTYPES.PROCEEDINGS, ENTRYTYPES.INBOOK]
        ),
        Fields.CONTAINER_TITLE,
    ] = records_df[Fields.BOOKTITLE]
    records_df.loc[
        records_df[Fields.ENTRYTYPE] == ENTRYTYPES.BOOK, Fields.CONTAINER_TITLE
    ] = records_df[Fields.TITLE]
    records_df[Fields.CONTAINER_TITLE].fillna("", inplace=True)


def get_author_format_case(authors: typing.List[str], original_string: str) -> str:
    # TODO : non-latin, empty (after replacing special characters)
    if authors == [""]:
        return "empty"

    if any(
        keyword in original_string.lower() for keyword in ["group", "agency", "council"]
    ):
        return "organization"

    if (" and " in original_string and ", " in original_string) or (
        " and " not in original_string
        and ", " in original_string
        and len(original_string) < 50
    ):
        return "proper_format"

    if len(authors) < 4 and not any("," in a for a in authors):
        return "single_author_missing_comma"

    if " and " not in authors and "," not in authors and len(original_string) > 5:
        if (
            sum(
                1
                for author in authors
                if author.isupper() and len(re.sub(r"[ .-]", "", author)) < 3
            )
            / len(authors)
            >= 0.4
        ):
            return "abbreviated_initials"

        if (
            sum(1 for match in re.findall(r"[A-Z][a-z\.]+[A-Z][a-z]+", original_string))
            / len(authors)
            >= 0.1
        ):
            if " " in original_string:
                return "missing_spaces_between_words"
            else:
                return "no_spaces_at_al"

    return "special_case"


def get_authors_split(authors: str) -> list:
    # single author
    if len(authors) < 15:
        if " " not in authors and re.search(r"[a-z]{3}[A-Z]", authors):
            return re.split(r"(?<=[a-z])(?=[A-Z])", authors)

        if authors.count(" ") <= 2:
            return authors.split(" ")

    authors_list = re.split(r"(?=[A-Z])", authors)  # .replace(".", ""))
    for i in range(len(authors_list) - 1):
        if (
            authors_list[i].endswith("-")
            or authors_list[i] in ["Mc", "Mac"]
            or (
                len(authors_list[i]) == 1
                and authors_list[i].isupper()
                and len(authors_list[i + 1]) == 1
                and authors_list[i + 1].isupper()
            )
        ):
            authors_list[i + 1] = authors_list[i] + authors_list[i + 1]
            authors_list[i] = ""
    return [author.rstrip() for author in authors_list if author != ""]


def prep_authors(authors_array: np.array, *, debug: bool = False) -> np.array:
    def preprocess_author(authors: str, *, debug: bool) -> str:
        authors = str(authors)

        # Many databases do not handle accents properly...
        authors = colrev.env.utils.remove_accents(input_str=authors)
        authors = authors.replace("nan", "").replace("Anonymous", "")

        if ";" in authors:
            authors = authors.replace(";", " and ")

        # Capitalize von/van/... and add "-" to connect last-names
        authors = re.sub(
            r"([A-Z])(" + "|".join(NAME_PREFIXES_LOWER) + r") (\w+)",
            lambda match: match.group(1)
            + match.group(2).title().replace(" ", "-")
            + "-"
            + match.group(3),
            authors,
        )
        authors = re.sub(
            r"(^| |\.|-)(" + "|".join(NAME_PREFIXES_LOWER) + r") (\w+)",
            lambda match: match.group(1)
            + match.group(2).title().replace(" ", "-")
            + "-"
            + match.group(3),
            authors,
            flags=re.IGNORECASE,
        )

        original_string = authors

        authors_list = get_authors_split(authors)

        format_case = get_author_format_case(authors_list, original_string)

        if debug:
            print(original_string)
            print(f"format_case: {format_case}")
            print(f"authors_list: {authors_list}")

        if format_case == "proper_format":
            authors_str = authors
        elif format_case == "organization":
            authors_str = authors

        elif format_case == "empty":
            authors_str = ""

        elif format_case == "single_author_missing_comma":
            if authors_list[0].isupper():
                authors_list[0] = authors_list[0].title()

            authors_str = authors_list[0] + ", " + " ".join(authors_list[1:])

        # Broadley K.Burton A. C.Avgar T.Boutin S.
        elif format_case == "abbreviated_initials":
            temp_authors_list = []
            next_author: typing.List[str] = []
            for author_fragment in authors_list:
                if re.match(r"[A-Z][a-z]+", author_fragment):
                    temp_authors_list.append(" ".join(next_author))
                    next_author = [author_fragment]
                else:
                    next_author.append(author_fragment)
            temp_authors_list.append(" ".join(next_author))
            temp_authors_list = [author for author in temp_authors_list if author != ""]

            for i in range(len(temp_authors_list)):
                words = temp_authors_list[i].split()
                for j in range(len(words) - 1, -1, -1):
                    if words[j].isupper() and not words[j - 1].isupper():
                        words[j - 1] = words[j - 1] + ","
                        break
                temp_authors_list[i] = " ".join(words)

            for i in range(len(temp_authors_list)):
                if i == len(temp_authors_list) - 1:
                    temp_authors_list[i] = temp_authors_list[i]
                elif ", " in temp_authors_list[i]:
                    temp_authors_list[i] = temp_authors_list[i] + " and "
                else:
                    temp_authors_list[i] = temp_authors_list[i] + " "

            authors_str = "".join(temp_authors_list)

        # PayenJ.-L.IzopetJ.Galindo-MigeotV.Lauwers-Cances
        elif format_case == "no_spaces_at_al":
            authors_str = ""
            for element in authors_list:
                if re.match(r"^[A-Z][a-z]+", element):
                    authors_str += element + " "
                else:
                    authors_str += ", " + element + " and "
            authors_str = authors_str.rstrip(" and ")

        # Vernia FilippoDi Ruscio MirkoStefanelli GianpieroViscido AngeloFrieri GiuseppeLatella Giovanni
        elif format_case == "missing_spaces_between_words":
            new_authors = re.sub(
                r"([A-Z][a-z.]+)([A-Z])", r"\1 SPLIT\2", original_string
            ).split("SPLIT")
            for i, element in enumerate(new_authors):
                words = [
                    w.replace(".", "").rstrip()
                    for w in element.split()
                    if w.lower() not in NAME_PREFIXES_LOWER
                ]
                if len(words) > 1:
                    middle_index = len(words) // 2
                    words.insert(middle_index, ",")
                    new_authors[i] = " ".join(words)
            authors_str = " and ".join(new_authors)

        else:
            print(f"{format_case}: {original_string}")
            authors_str = " and ".join(authors_list)

        authors_str = authors_str.replace(" ,", ",")
        authors_str = authors_str.replace("anonymous", "").replace("jr", "")
        authors_str = re.sub(r"[^A-Za-z0-9, ]+", "", authors_str)
        return authors_str.lower()

    return np.array(
        [preprocess_author(author, debug=debug) for author in authors_array]
    )


def select_authors(authors_array: np.array) -> np.array:
    def select_author(authors: str) -> str:
        """Select first author"""

        authors_list = authors.split(" and ")
        authors_str = " ".join(
            [
                re.sub(
                    r"(^| )(" + "|".join(NAME_PREFIXES_LOWER) + r") ",
                    lambda match: match.group(1) + match.group(2).replace(" ", "-") + "-",  # type: ignore
                    author.split(",")[0],
                    flags=re.IGNORECASE,
                ).replace(" ", "")
                for author in authors_list
            ][:8]
        )
        authors_str = authors_str.replace("anonymous", "").replace("jr", "")
        authors_str = re.sub(r"[^A-Za-z0-9, ]+", "", authors_str)
        return authors_str

    return np.array([select_author(author) for author in authors_array])


def remove_erratum_suffix(title: str) -> str:
    erratum_phrases = ["erratum appears in ", "erratum in "]
    for phrase in erratum_phrases:
        if phrase in title:
            title = title.split(phrase)[0]

    title = re.sub(r" review \d+ refs$", "", title)

    return title


def prep_title(title_array: np.array) -> np.array:
    # Remove html tags
    title_array = np.array([re.sub(r"<.*?>", " ", title) for title in title_array])
    # Remove language tags added by some databases (at the end)
    title_array = np.array(
        [re.sub(r"\. \[[A-Z][a-z]*\]$", "", title) for title in title_array]
    )

    # Remove erratum suffix
    # https://www.nlm.nih.gov/bsd/policy/errata.html
    title_array = np.array([remove_erratum_suffix(title) for title in title_array])

    # Remove '[Review] [33 refs]' and ' [abstract no: 134]' ignoring case
    title_array = np.array(
        [
            re.sub(
                r"\[Review\] \[\d+ refs\]| \[abstract no: \d+\]",
                "",
                title,
                flags=re.IGNORECASE,
            )
            for title in title_array
        ]
    )

    # Replace roman numbers and special characters
    title_array = np.array(
        [
            re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9, \[\]]+", " ", title.lower()))
            .replace(" iv ", " 4 ")
            .replace(" iii ", " 3 ")
            .replace(" ii ", " 2 ")
            .replace(" i ", " 1 ")
            .rstrip()
            .lstrip()
            for title in title_array
        ]
    )
    # Replace spaces between digits
    title_array = np.array(
        [
            re.sub(r"(\d) (\d)", r"\1\2", title).rstrip(" ].").lstrip("[ ")
            for title in title_array
        ]
    )
    return title_array


def prep_container_title(ct_array: np.array) -> np.array:
    ct_array = np.array(
        [
            parse(value).split(".")[0].replace("proceedings of the", "")
            if "date of publication" in value.lower()
            or "conference start" in value.lower()
            else parse(value).replace("proceedings of the", "")
            for value in ct_array
        ]
    )
    ct_array = np.array([value.replace(".", " ") for value in ct_array])
    ct_array = np.array([re.sub(r"[^A-Za-z -]+", " ", value) for value in ct_array])
    ct_array = np.array([re.sub(r"^the\s", "", value) for value in ct_array])
    ct_array = np.array(
        [re.sub(r"^\s*(st|nd|rd|th) ", "", value) for value in ct_array]
    )
    return ct_array


def get_abbrev_container_title(ct_array: np.array) -> np.array:
    def get_abbrev(ct: str) -> str:
        # Use abbreviated versions
        # journal of infection and chemotherapy
        # j infect chemother

        ct = parse(str(ct))  # replace numbers
        match = re.search(r"\.\d+ \(", ct)
        if match:
            ct = ct[: match.start()]

        ct = ct.lower().replace("journal", "j").replace("-", " ")
        stopwords = ["of", "the", "and", "de", "d", "et", "in", "y", "i", "&", "on"]
        ct = " ".join(word[:4] for word in ct.split() if word not in stopwords)
        return ct

    return np.array([get_abbrev(ct) for ct in ct_array])


def prep_year(year_array: np.array) -> np.array:
    def process_year(value: str) -> str:
        try:
            return str(int(float(value)))
        except ValueError:
            return value

    return np.array([process_year(year) for year in year_array])


def prep_volume(volume_array: np.array) -> np.array:
    volume_array = np.array(
        [
            re.search(r"(\d+) \(.*\)", volume).group(1)  # type: ignore
            if re.search(r"(\d+) \(.*\)", volume) is not None
            else volume
            for volume in volume_array
        ]
    )
    volume_array = np.array(
        [
            re.search(r"(\d+) suppl \d+", volume.lower()).group(1)  # type: ignore
            if re.search(r"(\d+) suppl \d+", volume.lower()) is not None
            else volume
            for volume in volume_array
        ]
    )
    volume_array = np.array(
        [
            re.sub(r"[^\d]", "", volume)
            if len(volume) < 4 and any(char.isdigit() for char in volume)
            else volume
            for volume in volume_array
        ]
    )
    return np.array(["" if volume == "nan" else volume for volume in volume_array])


def prep_number(number_array: np.array) -> np.array:
    number_array = np.array(
        [re.sub(r"[A-Za-z.]*", "", number) for number in number_array]
    )

    return np.array(
        ["" if number in ["nan", "var.pagings"] else number for number in number_array]
    )


def prep_pages(pages_array: np.array) -> np.array:
    def process_page(value: str) -> str:
        if value.isalpha():
            return ""

        value = re.sub(r"S(\d+)", r"\1", value)
        if re.match(r"^(pp\.?|)\d+\s*-?-\s*\d+$", value):
            from_page, to_page = re.findall(r"(\d+)", value)
            if len(from_page) > len(to_page):
                return f"{from_page}-{from_page[:-len(to_page)]}{to_page}"
            else:
                return f"{from_page}-{to_page}"
        if value in [
            " ",
            None,
            "nan",
            "na",
            "no pages",
            "no pagination",
            "var.pagings",
        ]:
            return ""
        return value

    return np.vectorize(process_page)(pages_array)


def prep_abstract(abstract_array: np.array) -> np.array:
    abstract_array = np.array(
        [re.sub(r"<.*?>", " ", abstract.lower()) for abstract in abstract_array]
    )
    abstract_array = np.array(
        [
            abstract[: abstract.rfind("copyright") - 3]
            if "copyright" in abstract[-200:]
            else abstract[: abstract.rfind("©") - 3]
            if "©" in abstract[-200:]
            else abstract
            for abstract in abstract_array
        ]
    )
    abstract_array = np.array(
        [re.sub(r"[^A-Za-z0-9 .,]", "", abstract) for abstract in abstract_array]
    )
    abstract_array = np.array(
        [re.sub(r"\s+", " ", abstract) for abstract in abstract_array]
    )
    return np.array(
        [
            "" if abstract == "nan" else abstract.lower().rstrip(" .")
            for abstract in abstract_array
        ]
    )


def prep_doi(doi_array: np.array) -> np.array:
    doi_array = np.array(
        [re.sub(r"http://dx.doi.org/", "", doi.lower()) for doi in doi_array]
    )
    doi_array = np.array([re.sub(r"\[doi\]", "", doi) for doi in doi_array])
    doi_array = np.array(
        [doi.split("[pii];")[1] if "[pii];" in doi else doi for doi in doi_array]
    )
    doi_array = np.array([urllib.parse.unquote(doi) for doi in doi_array])
    return np.array(["" if doi == "nan" else doi.rstrip() for doi in doi_array])


def prep_isbn(isbn_array: np.array) -> np.array:
    isbn_array = np.array([re.sub(r"[\r\n]", ";", isbn) for isbn in isbn_array])
    return np.array(["" if isbn == "nan" else isbn.lower() for isbn in isbn_array])


def get_short_container_title(ct_array: np.array) -> np.array:
    return np.array(
        ["".join(item[0] for item in ct.split() if item.isalpha()) for ct in ct_array]
    )
