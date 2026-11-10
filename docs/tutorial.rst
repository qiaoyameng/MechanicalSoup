MechanicalSoup tutorial
=======================

First contact, step by step
---------------------------

As a simple example, we'll browse http://httpbin.org/, a website
designed to test tools like MechanicalSoup.

First, let's create a browser object::

  >>> import mechanicalsoup
  >>> browser = mechanicalsoup.StatefulBrowser()

To customize the way to build a browser (change the user-agent, the
HTML parser to use, the way to react to 404 Not Found errors, ...),
see :func:`~mechanicalsoup.StatefulBrowser.__init__`.

Now, open the webpage we want::

  >>> browser.open("http://httpbin.org/")
  <Response [200]>

The return value of :func:`~mechanicalsoup.StatefulBrowser.open` is an
object of type requests.Response_. Actually, MechanicalSoup is using
the requests_ library to do the actual requests to the website, so
there's no surprise that we're getting such object. In short, it
contains the data and meta-data that the server sent us. You see the
HTTP response status, 200, which means "OK", but the object also
contains the content of the page we just downloaded.

Just like a normal browser's URL bar, the browser remembers which URL
it's browsing::

  >>> browser.url
  'http://httpbin.org/'

Now, let's follow the link to ``/forms/post``::

  >>> browser.follow_link("forms")
  <Response [200]>
  >>> browser.url
  'http://httpbin.org/forms/post'

We passed a regular expression ``"forms"``
to :func:`~mechanicalsoup.StatefulBrowser.follow_link`, who followed
the link whose text matched this expression. There are many other ways
to call :func:`~mechanicalsoup.StatefulBrowser.follow_link`, but we'll
get back to it.

We're now visiting http://httpbin.org/forms/post, which contains a
form. Let's see the page content::

  >>> browser.page
  <!DOCTYPE html>
  <html>
  ...
  <form action="/post" method="post">
  ...

Actually, the return type
of :func:`~mechanicalsoup.StatefulBrowser().page` is
bs4.BeautifulSoup_. BeautifulSoup, aka bs4, is the second library used
by Mechanicalsoup: it is an HTML manipulation library. You can now
navigate in the tags of the pages using BeautifulSoup. For example, to
get all the ``<legend>`` tags::

  >>> browser.page.find_all('legend')
  [<legend> Pizza Size </legend>, <legend> Pizza Toppings </legend>]

To fill-in a form, we need to tell MechanicalSoup which form we're
going to fill-in and submit::

  >>> browser.select_form('form[action="/post"]')

The argument to :func:`~mechanicalsoup.StatefulBrowser.select_form` is
a CSS selector. Here, we select an HTML tag named ``form`` having an
attribute ``action`` whose value is ``"/post"``. Since there's only
one form in the page, ``browser.select_form()`` would have done the
trick too.

Now, give a value to fields in the form. First, what are the available
fields? You can print a summary of the currently selected form
with :func:`~mechanicalsoup.Form.print_summary()`::

  >>> browser.form.print_summary()
  <input name="custname"/>
  <input name="custtel" type="tel"/>
  <input name="custemail" type="email"/>
  <input name="size" type="radio" value="small"/>
  <input name="size" type="radio" value="medium"/>
  <input name="size" type="radio" value="large"/>
  <input name="topping" type="checkbox" value="bacon"/>
  <input name="topping" type="checkbox" value="cheese"/>
  <input name="topping" type="checkbox" value="onion"/>
  <input name="topping" type="checkbox" value="mushroom"/>
  <input max="21:00" min="11:00" name="delivery" step="900" type="time"/>
  <textarea name="comments"></textarea>

For text fields, it's simple:
just give a value for ``input`` element based on their ``name``
attribute::

  >>> browser["custname"] = "Me"
  >>> browser["custtel"] = "00 00 0001"
  >>> browser["custemail"] = "nobody@example.com"
  >>> browser["comments"] = "This pizza looks really good :-)"

For radio buttons, well, it's simple too: radio buttons have several
``input`` tags with the same ``name`` and different values, just select
the one you need (``"size"`` is the ``name`` attribute, ``"medium"``
is the ``"value"`` attribute of the element we want to tick)::

  >>> browser["size"] = "medium"

For checkboxes, one can use the same mechanism to check one box::

  >>> browser["topping"] = "bacon"

But we can also check any number of boxes by assigning a list to the
field::

  >>> browser["topping"] = ("bacon", "cheese")

Actually, ``browser["..."] = "..."`` (i.e. calls
to :func:`~mechanicalsoup.StatefulBrowser.__setitem__`) is just a
helper to fill-in a form, but you can use any tool BeautifulSoup
provides to modify the soup object, and MechanicalSoup will take care
of submitting the form for you.

Let's see what the filled-in form looks like::

  >>> browser.launch_browser()

:func:`~mechanicalsoup.StatefulBrowser.launch_browser` will launch a
real web browser on the current page visited by our ``browser``
object, including the changes we just made to the form (note that it
does not open the real webpage, but creates a temporary file
containing the page content, and points your browser to this file). Try
changing the boxes ticked and the content of the text field, and
re-launch the browser.

This method is very useful in complement with your browser's web
development tools. For example, with Firefox, right-click "Inspect
Element" on a field will give you everything you need to manipulate
this field (in particular the ``name`` and ``value`` attributes).

It's also possible to check the content
with :func:`~mechanicalsoup.Form.print_summary()` (that we already
used to list the fields)::

  >>> browser.form.print_summary()
  <input name="custname" value="Me"/>
  <input name="custtel" type="tel" value="00 00 0001"/>
  <input name="custemail" type="email" value="nobody@example.com"/>
  <input name="size" type="radio" value="small"/>
  <input checked="" name="size" type="radio" value="medium"/>
  <input name="size" type="radio" value="large"/>
  <input checked="" name="topping" type="checkbox" value="bacon"/>
  <input checked="" name="topping" type="checkbox" value="cheese"/>
  <input name="topping" type="checkbox" value="onion"/>
  <input name="topping" type="checkbox" value="mushroom"/>
  <input max="21:00" min="11:00" name="delivery" step="900" type="time"/>
  <textarea name="comments">This pizza looks really good :-)</textarea>

Assuming we're satisfied with the content of the form, we can submit
it (i.e. simulate a click on the submit button)::

  >>> response = browser.submit_selected()

The response is not an HTML page, so the browser doesn't parse it to a
BeautifulSoup object, but we can still see the text it contains::

  >>> print(response.text)
  {
    "args": {},
    "data": "",
    "files": {},
    "form": {
      "comments": "This pizza looks really good :-)",
      "custemail": "nobody@example.com",
      "custname": "Me",
      "custtel": "00 00 0001",
      "delivery": "",
      "size": "medium",
      "topping": [
        "bacon",
        "cheese"
      ]
    },
  ...

To sum up, here is the complete example (`examples/expl_httpbin.py
<https://github.com/MechanicalSoup/MechanicalSoup/blob/main/examples/expl_httpbin.py>`__):

.. literalinclude:: ../examples/expl_httpbin.py

Reusing a form: stateful mode, defaults and reset
-------------------------------------------------

By default, filling a field mutates the corresponding BeautifulSoup tag
in place: assigning a new ``value`` overwrites the attribute that held
the page's original value, so the initial value is lost. This is fine
for filling a form once, but it gets in the way when the same form has
to be filled several times with different sets of conditions and then
restored.

Passing ``stateful=True`` to
:func:`~mechanicalsoup.StatefulBrowser.select_form` (or constructing a
:class:`~mechanicalsoup.Form` directly with ``Form(tag, stateful=True)``)
switches the form to *stateful mode*. It then keeps two values for every
control:

* the **current** value, which is what the ``set_*`` methods, the read
  interface and the final request preparation all use;
* the **default** value, a snapshot of the control when the form was
  selected (or the last explicit default update).

Each control edited through a ``set_*`` method (or ``browser["name"] =
...``) is flagged *dirty*. The same form can then be filled repeatedly,
and :func:`~mechanicalsoup.Form.reset` restores the current defaults
purely from memory, without re-requesting the page::

  >>> browser.select_form('form[action="/post"]', stateful=True)
  >>> browser["custname"] = "First attempt"
  >>> browser.form.is_dirty()
  True
  >>> browser["custname"] = "Second attempt"
  >>> browser.form.reset()
  >>> browser.form.is_dirty()
  False

Reset follows the HTML control rules: text inputs and textareas regain
their default text, checkboxes/radios their default checked set, and
single/multiple ``<select>`` elements their default selection. File
inputs are always cleared (a browser cannot pre-select a file), while
the ``disabled`` attribute and the order of controls are never touched.

The defaults themselves can be changed explicitly with
:func:`~mechanicalsoup.Form.update_defaults`, which accepts the same
value conventions as the setters. A control that has not been edited
follows the new default immediately; a dirty control keeps its edit and
only adopts the new default on the next reset::

  >>> browser["comments"] = "work in progress"
  >>> browser.form.update_defaults({
  ...     "custname": "default customer",
  ...     "comments": "default comment",
  ... })
  >>> # "comments" is dirty: the edit is preserved until reset().

Calling :func:`~mechanicalsoup.Form.update_defaults` with no argument (or
:func:`~mechanicalsoup.Form.sync_defaults`) takes the current DOM values
as the new baseline and clears every dirty flag.

Modifying the BeautifulSoup tags directly
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The state layer only observes changes made through the form's ``set_*``
methods. If you edit the underlying tags yourself (for example
``browser.form.form.find("input", {"name": "custname"})["value"] =
"x"``), the state layer is not aware of it and a reset would overwrite
your edit. After such direct DOM modifications, call
:func:`~mechanicalsoup.Form.sync_defaults` to adopt the current DOM as
the new default baseline. Alternatively, construct the
:class:`~mechanicalsoup.Form` only after the external edits, so they are
captured as the initial defaults.

.. _requests: http://docs.python-requests.org/en/master/
.. _requests.Response: http://docs.python-requests.org/en/master/api/#requests.Response
.. _bs4.BeautifulSoup: https://www.crummy.com/software/BeautifulSoup/bs4/doc/#beautifulsoup

A more complete example: logging-in into GitHub
-----------------------------------------------

The simplest way to use MechanicalSoup is to use
the :class:`~mechanicalsoup.StatefulBrowser` class (this example is
available as `examples/example.py
<https://github.com/MechanicalSoup/MechanicalSoup/blob/main/examples/example.py>`__
in MechanicalSoup's source code):

.. literalinclude:: ../examples/example.py
   :language: python

Alternatively, one can use the :class:`~mechanicalsoup.Browser` class,
which doesn't maintain a state from one call to another (i.e. the
Browser itself doesn't remember which page you are visiting and what
its content is, it's up to the caller to do so). This example is
available as `examples/example_manual.py
<https://github.com/MechanicalSoup/MechanicalSoup/blob/main/examples/example_manual.py>`__
in the source:

.. literalinclude:: ../examples/example_manual.py
   :language: python

More examples
~~~~~~~~~~~~~

For more examples, see the `examples
<https://github.com/MechanicalSoup/MechanicalSoup/blob/main/examples/>`__
directory in MechanicalSoup's source code.
